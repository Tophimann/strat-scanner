"""
Stock Strategy EOD Scanner — The Strat (Multi-TF Chain, Long Only)
====================================================================
Run this after US market close each day.

Strategy: top-down chain scan  Monthly -> Weekly -> Daily -> 4H -> 1H
- Monthly: gate only (must be 2u or 3; coiling = 2u-1 or 3-1)
- Weekly non-coiling & aligned: enter here, stop drilling (P2/P3)
- Weekly coiling + monthly coiling: both flag P1-DOUBLE, drill to Daily
- Weekly coiling only: flag P2-W-COIL, drill to Daily
- Daily signal: try 4H first (tighter stop) — fall back to daily if 4H flat
- Daily coiling: drill to 4H (4H resampled from 1H data)
- 4H coiling: drill to 1H
- 1H: terminal entry TF

Entry: Buy Stop at signal candle HIGH
Stop:  Signal candle LOW (min 0.3% of entry price)
FTC:   Monthly and Weekly both bullish (2u or 3) — required gate

Usage:
    python stock_strategy_scanner.py

Output:
    stock_strategy_log_YYYY-MM-DD.csv
"""

import json
import csv
import os
from datetime import datetime, date
import yfinance as yf
try:
    from db_writer import upsert_scan_results as _db_upsert_scans
except ImportError:
    _db_upsert_scans = None
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

# ── Config ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
SYMBOLS_FILE    = os.path.join(SCRIPT_DIR, "stock_strategy_symbols.json")
ACCOUNT_EQUITY  = 98851.80          # Update daily (excl. floating P&L)
RISK_PCT        = 0.01              # 1% risk per trade
DAILY_LOSS_LIMIT_PCT = 0.05         # 5% daily loss limit
RR_TARGET       = 2.0              # Minimum reward:risk
MIN_STOP_PCT    = 0.003             # 0.3% minimum stop size
MAX_RISK_PCT    = 0.20              # 20% max entry-stop spread

# Coiling = directional bar followed by inside bar (compression expecting breakout)
COILING_COMBOS  = {'2u-1', '3-1'}
# Valid long signal bar types (last bar of TF)
VALID_LONG_BARS = {'1', '2u', '3'}
# Bullish FTC bar types (gate check)
BULLISH_BARS    = {'2u', '3'}

# ── Candle Classification ──────────────────────────────────────────────────────
def classify(h, l, ph, pl):
    """Classify a candle relative to the prior candle."""
    if h <= ph and l >= pl:
        return "1"    # Inside bar
    elif h > ph and l < pl:
        return "3"    # Outside bar
    elif h > ph:
        return "2u"   # Directional up
    else:
        return "2d"   # Directional down


# ── TF Evaluation ──────────────────────────────────────────────────────────────
def eval_tf(df):
    """
    Evaluate the last 2-3 closed bars of a TF dataframe.
    Returns: (state, last_bar_type, two_bar_combo, df_tail)
      state: 'coiling' | 'signal' | 'bearish' | None
      last_bar_type: e.g. '1', '2u', '3', '2d'
      two_bar_combo: e.g. '2u-1', '3-2u'
      df_tail: DataFrame with recent bars (for target calc)
    """
    df = df.dropna()
    # Drop current incomplete bar (FTC rule: closed bars only)
    df = df.iloc[:-1]
    if len(df) < 2:
        return None, None, None, df

    cur = df.iloc[-1]
    prv = df.iloc[-2]

    cur_type = classify(float(cur['High']), float(cur['Low']),
                        float(prv['High']), float(prv['Low']))

    two_bar = None
    if len(df) >= 3:
        prv2 = df.iloc[-3]
        prv_type = classify(float(prv['High']), float(prv['Low']),
                            float(prv2['High']), float(prv2['Low']))
        two_bar = f"{prv_type}-{cur_type}"

    if two_bar in COILING_COMBOS:
        state = 'coiling'
    elif cur_type in VALID_LONG_BARS:
        state = 'signal'
    elif cur_type == '2d':
        state = 'bearish'
    else:
        state = None

    return state, cur_type, two_bar, df


# ── 4H Resampling ──────────────────────────────────────────────────────────────
def resample_4h(df_1h):
    """Resample 1H OHLCV bars into 4H bars using pandas resample."""
    df = df_1h.copy()
    # Ensure DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index('Datetime') if 'Datetime' in df.columns else df.set_index('Date')
    df = df.sort_index()
    df_4h = df.resample('4h', closed='left', label='left').agg({
        'Open':   'first',
        'High':   'max',
        'Low':    'min',
        'Close':  'last',
        'Volume': 'sum'
    }).dropna()
    return df_4h


# ── Priority Scoring ───────────────────────────────────────────────────────────
def calc_priority(coiling_tfs):
    """
    coiling_tfs: list of TF names that were coiling, e.g. ['monthly', 'weekly']
    Returns priority string.
    """
    n = len(coiling_tfs)
    if n >= 3:
        return "P1-TRIPLE"
    elif n == 2:
        return "P1-DOUBLE"
    elif n == 1:
        tf = coiling_tfs[0]
        return f"P2-{tf[:1].upper()}-COIL"
    else:
        return "P3-ALIGNED"


# ── Position Sizing ────────────────────────────────────────────────────────────
def calc_position(equity, entry, stop):
    """1% equity risk per trade."""
    risk_dollars  = equity * RISK_PCT
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0, 0
    shares = int(risk_dollars / risk_per_share)
    actual_risk = shares * risk_per_share
    return shares, round(actual_risk, 2)


# ── Target Calculation ─────────────────────────────────────────────────────────
def strat_target(df_full, entry, stop):
    """
    Primary:   2:1 RR minimum
    Secondary: nearest prior swing high above 2:1 in last 20 bars
    """
    risk = entry - stop
    min_target = entry + RR_TARGET * risk
    prior_highs = [h for h in df_full['High'].tail(20).values if h > min_target]
    if prior_highs:
        nearest = min(prior_highs)
        if nearest >= min_target:
            return round(nearest, 4)
    return round(min_target, 4)


# ── Extract Single-Symbol Slice ────────────────────────────────────────────────
def get_sym_df(raw, yf_sym, n_symbols):
    """Extract one symbol from a batch yfinance download."""
    if n_symbols == 1:
        return raw.copy()
    if yf_sym not in raw.columns.get_level_values(0):
        return None
    df = raw[yf_sym].copy()
    return df


# ── Build Entry Row ────────────────────────────────────────────────────────────
def build_entry(yf_sym, tv_sym, today_str, df_entry, entry_tf,
                ftc_m_type, ftc_w_type, coiling_tfs, combo_3bar):
    """
    Validate entry levels, apply position sizing, build CSV row.
    Returns dict or None if invalid.
    """
    df = df_entry.dropna().iloc[:-1]   # closed bars only
    if len(df) < 2:
        return None

    cur = df.iloc[-1]
    entry = round(float(cur['High']), 4)
    stop_raw = round(float(cur['Low']), 4)

    # Enforce minimum stop size (0.3% of entry)
    min_stop_dist = entry * MIN_STOP_PCT
    stop = min(stop_raw, round(entry - min_stop_dist, 4))

    risk = entry - stop
    if risk <= 0 or (risk / entry) > MAX_RISK_PCT:
        return None

    target = strat_target(df, entry, stop)
    rr     = round((target - entry) / risk, 2)
    if rr < RR_TARGET:
        return None

    shares, risk_dollars = calc_position(ACCOUNT_EQUITY, entry, stop)
    if shares == 0:
        return None

    priority = calc_priority(coiling_tfs)

    # Build HTF setup description e.g. "M:2u-1 W:3-1"
    htf_parts = []
    if ftc_m_type:
        htf_parts.append(f"M:{ftc_m_type}")
    if ftc_w_type:
        htf_parts.append(f"W:{ftc_w_type}")
    htf_setup = " ".join(htf_parts)

    # signal_date
    if hasattr(cur.name, 'date'):
        signal_date = str(cur.name.date())
    else:
        signal_date = today_str

    return {
        "date":          today_str,
        "symbol_tv":     tv_sym,
        "symbol":        yf_sym,
        "priority":      priority,
        "entry_tf":      entry_tf,
        "htf_setup":     htf_setup,
        "combo":         combo_3bar or "",
        "ftc_monthly":   ftc_m_type or "",
        "ftc_weekly":    ftc_w_type or "",
        "signal_date":   signal_date,
        "entry":         entry,
        "stop":          stop,
        "target":        target,
        "risk_share":    round(risk, 4),
        "rr":            rr,
        "shares":        shares,
        "risk_dollars":  risk_dollars,
        "position_val":  round(shares * entry, 2),
        "status":        "pending",
        "filled_price":  "",
        "exit_price":    "",
        "pnl":           "",
        "notes":         ""
    }


# ── 3-Bar Combo Builder ────────────────────────────────────────────────────────
def build_3bar_combo(df):
    """
    Build the 3-bar combo string (e.g. '2u-1-2u') from the last 3 closed bars.
    df should be the raw TF frame (closed-bar filter applied internally).
    Returns None if fewer than 4 bars available.
    """
    df2 = df.dropna().iloc[:-1]   # exclude current incomplete bar
    if len(df2) < 4:
        return None
    c0 = classify(float(df2.iloc[-1]['High']), float(df2.iloc[-1]['Low']),
                  float(df2.iloc[-2]['High']), float(df2.iloc[-2]['Low']))
    c1 = classify(float(df2.iloc[-2]['High']), float(df2.iloc[-2]['Low']),
                  float(df2.iloc[-3]['High']), float(df2.iloc[-3]['Low']))
    c2 = classify(float(df2.iloc[-3]['High']), float(df2.iloc[-3]['Low']),
                  float(df2.iloc[-4]['High']), float(df2.iloc[-4]['Low']))
    return f"{c2}-{c1}-{c0}"


# ── Chain Evaluator ────────────────────────────────────────────────────────────
def evaluate_chain(yf_sym, tv_sym, today_str,
                   df_m, df_w, df_d, df_1h):
    """
    Walk the chain: Monthly -> Weekly -> Daily -> 4H -> 1H.

    Signal drill-down rule:
      When a TF shows a plain signal (not coiling), always try one TF lower
      for a tighter entry before accepting the current TF.
      Currently implemented: daily-signal → try 4H → fall back to daily.
      If 4H also signals, the 4H entry is used (tighter stop, same $risk →
      more shares, better RR). notes field flagged 'D-sig→4H-tighter'.

    Returns a list of setup dicts (one per symbol). Empty list = no setup.
    """
    setups = []
    coiling_tfs = []   # accumulate coiling levels as we drill down

    # ── MONTHLY gate ──────────────────────────────────────────────────────
    m_state, m_bar, m_combo, df_m_clean = eval_tf(df_m)

    # Must be bullish or coiling to proceed
    if m_bar not in BULLISH_BARS and m_state != 'coiling':
        return []
    if m_state == 'coiling':
        coiling_tfs.append('monthly')
    ftc_m_type = m_combo if m_state == 'coiling' else m_bar

    # ── WEEKLY ────────────────────────────────────────────────────────────
    w_state, w_bar, w_combo, df_w_clean = eval_tf(df_w)

    if w_bar not in BULLISH_BARS and w_state != 'coiling':
        return []   # Weekly bearish — no setup

    if w_state == 'coiling':
        coiling_tfs.append('weekly')
        ftc_w_type = w_combo
    else:
        ftc_w_type = w_bar

    # If weekly is a plain signal (non-coiling), enter here and STOP
    if w_state == 'signal':
        w3_combo = build_3bar_combo(df_w)
        if w3_combo and w3_combo == "2u-2u-2u":
            return []
        row = build_entry(yf_sym, tv_sym, today_str, df_w,
                          'weekly', ftc_m_type, ftc_w_type,
                          list(coiling_tfs), w3_combo)
        if row:
            setups.append(row)
        return setups   # STOP drilling if weekly gave a plain signal

    # Weekly is coiling → drill to DAILY
    if df_d is None or df_d.empty:
        return setups

    d_state, d_bar, d_combo, df_d_clean = eval_tf(df_d)

    if d_bar not in BULLISH_BARS and d_state != 'coiling':
        # Daily is bearish or None — skip drill but weekly coil is noted
        # (no LTF entry possible)
        return setups

    if d_state == 'coiling':
        coiling_tfs.append('daily')

    # If daily is a plain signal → try 4H drill-down for tighter entry first.
    # Rationale: if 4H also shows a confirming signal bar, the 4H stop is
    # tighter (smaller risk per share → more shares for same $risk → better RR).
    # Fall back to daily entry only when 4H is flat/bearish.
    if d_state == 'signal':
        d3_combo = build_3bar_combo(df_d)
        if d3_combo and d3_combo == "2u-2u-2u":
            return setups

        # ── Try 4H first ──────────────────────────────────────────────────
        used_4h = False
        if df_1h is not None and not df_1h.empty:
            try:
                df_4h = resample_4h(df_1h)
            except Exception:
                df_4h = pd.DataFrame()

            if not df_4h.empty:
                h4_state, h4_bar, h4_combo, _ = eval_tf(df_4h)
                if h4_state == 'signal' and h4_bar in BULLISH_BARS:
                    h4_3combo = build_3bar_combo(df_4h)
                    if not (h4_3combo and h4_3combo == "2u-2u-2u"):
                        row = build_entry(yf_sym, tv_sym, today_str, df_4h,
                                          '4h', ftc_m_type, ftc_w_type,
                                          list(coiling_tfs), h4_3combo)
                        if row:
                            row['notes'] = 'D-sig->4H-tighter'
                            setups.append(row)
                            used_4h = True

        # ── Fall back to daily if 4H doesn't confirm ──────────────────────
        if not used_4h:
            row = build_entry(yf_sym, tv_sym, today_str, df_d,
                              'daily', ftc_m_type, ftc_w_type,
                              list(coiling_tfs), d3_combo)
            if row:
                setups.append(row)
        return setups

    # Daily is coiling → drill to 4H
    if df_1h is None or df_1h.empty:
        return setups

    try:
        df_4h = resample_4h(df_1h)
    except Exception:
        df_4h = pd.DataFrame()

    if not df_4h.empty:
        h4_state, h4_bar, h4_combo, df_4h_clean = eval_tf(df_4h)

        if h4_bar in BULLISH_BARS or h4_state == 'coiling':
            if h4_state == 'coiling':
                coiling_tfs.append('4h')

            if h4_state == 'signal':
                # Enter at 4H level (daily was coiling, 4H signals)
                h4_3combo = build_3bar_combo(df_4h)
                if not (h4_3combo and h4_3combo == "2u-2u-2u"):
                    row = build_entry(yf_sym, tv_sym, today_str, df_4h,
                                      '4h', ftc_m_type, ftc_w_type,
                                      list(coiling_tfs), h4_3combo)
                    if row:
                        setups.append(row)
                return setups

            # 4H is coiling → drill to 1H (terminal)
            h1_state, h1_bar, h1_combo, df_1h_clean = eval_tf(df_1h)

            if h1_bar in BULLISH_BARS or h1_state == 'coiling':
                if h1_state == 'coiling':
                    coiling_tfs.append('1h')
                h1_3combo = build_3bar_combo(df_1h)
                if not (h1_3combo and h1_3combo == "2u-2u-2u"):
                    row = build_entry(yf_sym, tv_sym, today_str, df_1h,
                                      '1h', ftc_m_type, ftc_w_type,
                                      list(coiling_tfs), h1_3combo)
                    if row:
                        setups.append(row)

    return setups


# ── Determine last completed trading day ──────────────────────────────────────
def last_trading_day(df_daily=None) -> date:
    """Return the date of the last CLOSED daily bar.

    Strategy:
    - If last bar date < today: use it (yfinance hasn't published today yet)
    - If last bar date == today AND after 20:30 UTC AND bar has real volume: use today
    - Otherwise step back to the previous bar (market still open or pre-market stub)
    Using 20:30 UTC (4:30pm ET) adds a 30-min buffer after the official 20:00 close
    to ensure yfinance has finished publishing the final bar.
    """
    from datetime import timezone, timedelta
    today = date.today()

    if df_daily is not None and not df_daily.empty:
        last_bar = df_daily.index[-1]
        last_date = last_bar.date() if hasattr(last_bar, 'date') else last_bar.to_pydatetime().date()

        if last_date < today:
            # yfinance hasn't published today's bar yet — last close is last_date
            return last_date

        # last_date == today: check if the bar is genuinely complete
        now_utc = datetime.now(timezone.utc)
        market_close_utc = now_utc.replace(hour=20, minute=30, second=0, microsecond=0)

        # Also require that today's bar has meaningful volume (not a pre-market stub)
        try:
            vol_col = [c for c in df_daily.columns if 'volume' in str(c).lower() or c == 'Volume']
            today_volume = int(df_daily.iloc[-1][vol_col[0]]) if vol_col else 0
        except Exception:
            today_volume = 0

        if now_utc >= market_close_utc and today_volume > 10_000:
            return today          # bar is complete with real volume — use today

        # Market still open, pre-market stub, or too early — step back to previous bar
        if len(df_daily) >= 2:
            prev = df_daily.index[-2]
            return prev.date() if hasattr(prev, 'date') else prev.to_pydatetime().date()

    # Fallback: most recent completed weekday
    from datetime import timedelta
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:   # skip Saturday (5) and Sunday (6)
        d -= timedelta(days=1)
    return d


# ── Main Scanner ───────────────────────────────────────────────────────────────
def scan():
    today_str = date.today().strftime("%Y-%m-%d")   # run-date for filenames
    out_file  = os.path.join(SCRIPT_DIR, f"stock_strategy_log_{today_str}.csv")

    with open(SYMBOLS_FILE) as f:
        symbols_raw = json.load(f)

    def tv_to_yf(sym):
        return sym.split(":")[-1].replace(".", "-")

    symbols_yf = [tv_to_yf(s) for s in symbols_raw]
    sym_map    = {tv_to_yf(s): s for s in symbols_raw}
    n          = len(symbols_yf)

    print(f"\n{'='*60}")
    print(f"  Strat Long Scanner (Multi-TF Chain) -- EOD {today_str}")
    print(f"  Account equity : ${ACCOUNT_EQUITY:,.2f}")
    print(f"  Risk per trade : ${ACCOUNT_EQUITY * RISK_PCT:,.2f}")
    print(f"  Daily loss cap : ${ACCOUNT_EQUITY * DAILY_LOSS_LIMIT_PCT:,.2f}")
    print(f"  Scanning       : {n} symbols")
    print(f"{'='*60}\n")

    common = dict(auto_adjust=True, progress=False, group_by="ticker")

    # ── PASS 1: Monthly + Weekly (all symbols) ────────────────────────────
    print("Pass 1: Downloading monthly data (all symbols)...")
    raw_m = yf.download(symbols_yf, period="3y",  interval="1mo", **common)
    print("Pass 1: Downloading weekly data (all symbols)...")
    raw_w = yf.download(symbols_yf, period="3mo", interval="1wk", **common)

    # HTF filter: collect candidates that pass monthly + weekly gate
    candidates = []
    for yf_sym in symbols_yf:
        df_m = get_sym_df(raw_m, yf_sym, n)
        df_w = get_sym_df(raw_w, yf_sym, n)
        if df_m is None or df_w is None:
            continue
        m_state, m_bar, _, _ = eval_tf(df_m)
        if m_bar not in BULLISH_BARS and m_state != 'coiling':
            continue
        w_state, w_bar, _, _ = eval_tf(df_w)
        if w_bar not in BULLISH_BARS and w_state != 'coiling':
            continue
        candidates.append(yf_sym)

    print(f"  HTF candidates : {len(candidates)} / {n} passed monthly+weekly gate\n")

    # ── PASS 2: Daily + 1H (candidates only) ─────────────────────────────
    raw_d  = {}
    raw_1h = {}
    if candidates:
        print("Pass 2: Downloading daily data (candidates)...")
        dl_d = yf.download(candidates, period="20d", interval="1d", **common)
        print("Pass 2: Downloading 1H data (candidates)...")
        dl_1h = yf.download(candidates, period="7d", interval="1h", **common)

        nc = len(candidates)
        for yf_sym in candidates:
            raw_d[yf_sym]  = get_sym_df(dl_d,  yf_sym, nc)
            raw_1h[yf_sym] = get_sym_df(dl_1h, yf_sym, nc)

    # Determine scan date from actual data (last completed trading day)
    _ref_df = next((raw_d[s] for s in candidates if raw_d.get(s) is not None), None)
    scan_date = last_trading_day(_ref_df)
    scan_date_str = scan_date.strftime("%Y-%m-%d")
    if scan_date_str != today_str:
        print(f"  Scan date      : {scan_date_str} (last completed trading day)")

    # ── Chain evaluation ──────────────────────────────────────────────────
    all_setups = []
    errors     = []

    for yf_sym in candidates:
        tv_sym = sym_map[yf_sym]
        try:
            df_m  = get_sym_df(raw_m, yf_sym, n)
            df_w  = get_sym_df(raw_w, yf_sym, n)
            df_d  = raw_d.get(yf_sym)
            df_1h = raw_1h.get(yf_sym)

            rows = evaluate_chain(yf_sym, tv_sym, scan_date_str,
                                  df_m, df_w, df_d, df_1h)
            all_setups.extend(rows)
        except Exception as e:
            errors.append(f"{yf_sym}: {e}")

    # ── Sort: P1 first, then by RR descending ─────────────────────────────
    priority_order = {"P1-TRIPLE": 0, "P1-DOUBLE": 1,
                      "P2-M-COIL": 2, "P2-W-COIL": 3,
                      "P2-D-COIL": 4, "P2-4-COIL": 5,
                      "P3-ALIGNED": 6}
    all_setups.sort(key=lambda x: (
        priority_order.get(x['priority'], 9),
        -x['rr']
    ))

    # ── Write CSV ──────────────────────────────────────────────────────────
    if all_setups:
        fieldnames = list(all_setups[0].keys())
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_setups)

    # ── Write to Postgres (dashboard) ──────────────────────────────────────
    if not _db_upsert_scans:
        print("[DB] Skipped — db_writer not available (psycopg2/dotenv missing?)")
    elif all_setups:
        try:
            n = _db_upsert_scans(all_setups, scan_date=scan_date)
            print(f"[DB] {n} scan rows upserted to Postgres (date: {scan_date_str})")
        except Exception as _db_err:
            print(f"[DB] Warning: could not write to Postgres: {_db_err}")

    print(f"\n{'='*60}")
    print(f"  Setups found   : {len(all_setups)}")
    if all_setups:
        print(f"  Saved to       : {out_file}")
    if errors:
        print(f"  Errors         : {len(errors)}")
        for e in errors[:5]:
            print(f"    {e}")

    print(f"\n{'='*60}")
    print("TOP 15 SETUPS (by priority + RR):")
    print(f"{'-'*60}")
    for s in all_setups[:15]:
        print(f"  [{s['priority']:<12}] {s['symbol']:<8} TF:{s['entry_tf']:<6} "
              f"{s['combo']:<10} HTF:{s['htf_setup']:<14} "
              f"Entry:{s['entry']:<8} Stop:{s['stop']:<8} "
              f"Target:{s['target']:<8} RR:{s['rr']:<4} Shares:{s['shares']}")
    print(f"{'='*60}\n")

    return out_file, all_setups


if __name__ == "__main__":
    scan()
