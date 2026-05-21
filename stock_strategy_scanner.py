"""
TheStrat Daily Scanner v2.0
============================
Scans the Russell 1000 for Long setups on the daily chart after US market close.

Rules (v2.0):
  Gate 1 – FTC: last close > quarterly open AND monthly open AND weekly open
  Gate 2 – Liquidity: avg 20-day volume >= 500k, price >= $5
  Setups:  2-1-2 / 3-1-2 / 1-2-2 / 3-2-2 / Pivot Machine Gun
  Entry:   Buy Stop 1 cent above signal candle High
  Stop:    Low of signal candle
  Targets: T1 / T2 / T3 from key-level hierarchy (PDH->PWH->PMH->PQH->52WH->ATH)

Run after 22:00 CET (21:30 UTC) on weekdays via GitHub Actions.

Usage:
    python stock_strategy_scanner.py
"""

import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import warnings
warnings.filterwarnings("ignore")

# Add src/ to path (works both locally and on GitHub Actions)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "src"))

from data_loader import load_or_fetch, passes_liquidity
from ftc import get_ftc
from setup_scanner import scan_ticker

try:
    from db_writer import upsert_scan_results as _db_upsert
except ImportError:
    _db_upsert = None

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS_FILE  = os.path.join(_SCRIPT_DIR, "stock_strategy_symbols.json")
MIN_AVG_VOL   = 500_000
MIN_PRICE     = 5.0


# ── Determine last completed trading day ──────────────────────────────────────
def last_trading_day(df_daily: pd.DataFrame | None = None) -> date:
    """Return the date of the last CLOSED daily bar.

    - If last bar date < today: use it (yfinance hasn't published today yet)
    - If last bar date == today AND after 20:30 UTC AND real volume: use today
    - Otherwise step back to previous bar
    """
    today = date.today()

    if df_daily is not None and not df_daily.empty:
        last_bar  = df_daily.index[-1]
        last_date = last_bar.date() if hasattr(last_bar, 'date') else pd.Timestamp(last_bar).date()

        if last_date < today:
            return last_date

        now_utc = datetime.now(timezone.utc)
        close_utc = now_utc.replace(hour=20, minute=30, second=0, microsecond=0)

        try:
            vol_cols = [c for c in df_daily.columns if str(c).lower() == "volume"]
            today_vol = int(df_daily.iloc[-1][vol_cols[0]]) if vol_cols else 0
        except Exception:
            today_vol = 0

        if now_utc >= close_utc and today_vol > 10_000:
            return today

        if len(df_daily) >= 2:
            prev = df_daily.index[-2]
            return prev.date() if hasattr(prev, 'date') else pd.Timestamp(prev).date()

    # Fallback: last completed weekday
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ── Symbol helpers ────────────────────────────────────────────────────────────
def tv_to_yf(tv_sym: str) -> str:
    """Convert TradingView symbol (e.g. 'NASDAQ:AAPL') to Yahoo Finance format."""
    return tv_sym.split(":")[-1].replace(".", "-")


# ── Build output row ──────────────────────────────────────────────────────────
def build_row(yf_sym: str, tv_sym: str, scan_date_str: str, setup: dict) -> dict:
    """Merge scanner setup dict into a full output row for CSV and DB."""
    entry = setup["entry"]
    stop  = setup["stop"]
    t1    = setup.get("t1")

    return {
        # Identity
        "date":          scan_date_str,
        "symbol":        yf_sym,
        "symbol_tv":     tv_sym,
        # Setup
        "setup_type":    setup["setup_type"],
        "sequence":      setup["sequence"],
        "combo":         setup["sequence"],          # backward-compat alias
        "signal_date":   setup.get("signal_date", scan_date_str),
        # FTC
        "priority":      f"FTC-{setup['ftc_levels']}",
        "ftc_quarterly": setup.get("ftc_q", False),
        "ftc_monthly":   str(setup.get("ftc_m", False)).lower(),
        "ftc_weekly":    str(setup.get("ftc_w", False)).lower(),
        # Levels
        "entry":         entry,
        "stop":          stop,
        "risk_share":    round(entry - stop, 4),
        "target":        t1 or 0.0,                 # backward-compat (= T1)
        "last_close":    setup.get("last_close"),
        "t1":            t1,
        "t2":            setup.get("t2"),
        "t3":            setup.get("t3"),
        "rr":            setup.get("rr_t1", 0.0),   # backward-compat (= R:R T1)
        "rr_t1":         setup.get("rr_t1", 0.0),
        "rr_t2":         setup.get("rr_t2", 0.0),
        "rr_t3":         setup.get("rr_t3", 0.0),
        # Position sizing (computed client-side in dashboard)
        "shares":        0,
        "risk_dollars":  0.0,
        "position_val":  0.0,
        # Status
        "status":        "pending",
        "notes":         "",
    }


# ── Main Scanner ──────────────────────────────────────────────────────────────
def scan():
    today_str = date.today().strftime("%Y-%m-%d")
    out_file  = os.path.join(_SCRIPT_DIR, f"stock_strategy_log_{today_str}.csv")

    with open(SYMBOLS_FILE) as f:
        symbols_raw = json.load(f)

    symbols_yf = [tv_to_yf(s) for s in symbols_raw]
    sym_map    = {tv_to_yf(s): s for s in symbols_raw}
    n_total    = len(symbols_yf)

    print(f"\n{'='*60}")
    print(f"  TheStrat Daily Scanner v2.0  --  {today_str}")
    print(f"  Universe: {n_total} Russell 1000 symbols")
    print(f"  FTC: last close > Q/M/W open (all 3 required)")
    print(f"  Setups: 2-1-2 / 3-1-2 / 1-2-2 / 3-2-2 / Machine Gun")
    print(f"{'='*60}\n")

    # Determine scan date from a reference ticker (AAPL is always liquid)
    print("Loading reference data to determine scan date...")
    ref_df = load_or_fetch("AAPL", years=2)
    scan_date     = last_trading_day(ref_df)
    scan_date_str = scan_date.strftime("%Y-%m-%d")
    print(f"  Scan date: {scan_date_str}\n")

    # ── Scan each symbol ──────────────────────────────────────────────────
    all_setups = []
    n_ftc_pass = 0
    n_liquidity_fail = 0
    n_ftc_fail = 0
    errors     = []

    for i, yf_sym in enumerate(symbols_yf, 1):
        tv_sym = sym_map[yf_sym]
        try:
            # Load 2 years of daily data (parquet cache)
            df = load_or_fetch(yf_sym, years=2)
            if df.empty:
                continue

            # Gate 1: Liquidity filter
            if not passes_liquidity(df, MIN_AVG_VOL, MIN_PRICE):
                n_liquidity_fail += 1
                continue

            # Gate 2: FTC check
            ftc = get_ftc(df, scan_date)
            if not ftc["ftc_ok"]:
                n_ftc_fail += 1
                continue
            n_ftc_pass += 1

            # Setup detection
            setups = scan_ticker(df, scan_date, ftc)
            for s in setups:
                all_setups.append(build_row(yf_sym, tv_sym, scan_date_str, s))

        except Exception as e:
            errors.append(f"{yf_sym}: {e}")

        # Progress every 50 symbols
        if i % 50 == 0:
            print(f"  [{i}/{n_total}] setups so far: {len(all_setups)}")

    # ── Sort: FTC levels desc, then R:R T1 desc ───────────────────────────
    all_setups.sort(key=lambda x: (
        -x.get("rr_t1", 0),
        -(3 if x["priority"] == "FTC-3" else 2 if x["priority"] == "FTC-2" else 1),
    ))

    # ── Write CSV ──────────────────────────────────────────────────────────
    if all_setups:
        fieldnames = list(all_setups[0].keys())
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_setups)

    # ── Write to Postgres ─────────────────────────────────────────────────
    if not _db_upsert:
        print("[DB] Skipped -- db_writer not available")
    elif all_setups:
        try:
            n = _db_upsert(all_setups, scan_date=scan_date)
            print(f"[DB] {n} rows upserted (date: {scan_date_str})")
        except Exception as db_err:
            print(f"[DB] Warning: {db_err}")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Scan date      : {scan_date_str}")
    print(f"  Symbols scanned: {n_total}")
    print(f"  Liquidity fail : {n_liquidity_fail}")
    print(f"  FTC fail       : {n_ftc_fail}")
    print(f"  FTC pass       : {n_ftc_pass}")
    print(f"  Setups found   : {len(all_setups)}")
    if all_setups:
        print(f"  Saved to       : {out_file}")
    if errors:
        print(f"  Errors         : {len(errors)}")
        for e in errors[:5]:
            print(f"    {e}")

    print(f"\n{'='*60}")
    print("TOP 15 SETUPS (by R:R T1):")
    print(f"{'-'*60}")
    for s in all_setups[:15]:
        t1_str = f"{s['t1']:.2f}" if s['t1'] else "  --  "
        t2_str = f"{s['t2']:.2f}" if s['t2'] else "--"
        t3_str = f"{s['t3']:.2f}" if s['t3'] else "--"
        print(f"  [{s['priority']}] {s['symbol']:<7} {s['setup_type']:<10} "
              f"seq:{s['sequence']:<6} "
              f"entry:{s['entry']:<8.2f} stop:{s['stop']:<8.2f} "
              f"T1:{t1_str} ({s['rr_t1']:.1f}R)  "
              f"T2:{t2_str}  T3:{t3_str}")
    print(f"{'='*60}\n")

    return out_file, all_setups


if __name__ == "__main__":
    scan()
