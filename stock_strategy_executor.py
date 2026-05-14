"""
Stock Strategy Trade Executor — The Strat (Long Only)
======================================================
Run this at/near market open (9:30 AM ET) the day after the scanner.
Reads the latest stock_strategy_log_YYYY-MM-DD.csv, checks which setups
have triggered (price >= entry), and logs trades for Claude to execute
in TradingView paper trading.

Usage:
    python stock_strategy_executor.py

Output:
    stock_strategy_trades_YYYY-MM-DD.csv   (trades to execute)
    stock_strategy_log_YYYY-MM-DD.csv      (updated with status)
"""

import json
import csv
import os
import glob
try:
    from db_writer import upsert_trades as _db_upsert_trades, update_scan_statuses as _db_update_statuses
except ImportError:
    _db_upsert_trades = None
    _db_update_statuses = None
from datetime import datetime, date
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR           = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_EQUITY       = 98851.80
DAILY_LOSS_LIMIT_PCT = 0.05
DAILY_LOSS_CAP       = ACCOUNT_EQUITY * DAILY_LOSS_LIMIT_PCT   # ~$4,942

# ── Load setups from Postgres (primary when running on GitHub Actions) ────────
def load_setups_from_db():
    """Read today's pending ScanResult rows from Postgres. Returns list of
    setup dicts with the same keys as the CSV reader, or None on failure."""
    try:
        import psycopg2
        from dotenv import load_dotenv
        for _p in [os.path.join(SCRIPT_DIR, ".env"),
                   os.path.join(SCRIPT_DIR, "dashboard", ".env")]:
            if os.path.exists(_p):
                load_dotenv(_p, override=False)
                break
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return None
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        today = date.today()
        cur.execute("""
            SELECT symbol, "symbolTv", combo, priority, "tfChain",
                   entry, stop, target, "riskShare", rr, shares,
                   "riskDollars", "positionVal", status,
                   "ftcMonthly", "ftcWeekly"
            FROM "ScanResult"
            WHERE date = %s AND status = 'pending'
        """, (today,))
        rows = []
        for r in cur.fetchall():
            (symbol, symbol_tv, combo, priority, tf_chain,
             entry, stop, target, risk_share, rr, shares,
             risk_dollars, position_val, status,
             ftc_monthly, ftc_weekly) = r
            # Extract entry_tf and htf_setup from tfChain (format: "htf→entry_tf")
            parts = (tf_chain or "daily").split("→")
            entry_tf  = parts[-1] if parts else "daily"
            htf_setup = "→".join(parts[:-1]) if len(parts) > 1 else ""
            rows.append({
                "symbol":       symbol,
                "symbol_tv":    symbol_tv,
                "combo":        combo,
                "priority":     priority,
                "entry_tf":     entry_tf,
                "htf_setup":    htf_setup,
                "entry":        str(entry),
                "stop":         str(stop),
                "target":       str(target),
                "risk_share":   str(risk_share),
                "rr":           str(rr),
                "shares":       str(int(shares)),
                "risk_dollars": str(risk_dollars),
                "position_val": str(position_val),
                "status":       status,
                "ftc_monthly":  ftc_monthly or "",
                "ftc_weekly":   ftc_weekly or "",
                "filled_price": "",
                "exit_price":   "",
                "pnl":          "",
                "notes":        "",
            })
        cur.close()
        conn.close()
        print(f"[DB] Loaded {len(rows)} pending setups from Postgres for {today}")
        return rows if rows else None
    except Exception as e:
        print(f"[DB] Could not load setups from Postgres: {e}")
        return None


# ── Load latest scan log (local CSV fallback) ─────────────────────────────────
def load_latest_log():
    pattern = os.path.join(SCRIPT_DIR, "stock_strategy_log_*.csv")
    files   = sorted(glob.glob(pattern))
    if not files:
        print("No scan log found. Run stock_strategy_scanner.py first.")
        return None, None
    latest = files[-1]
    print(f"Loading: {latest}")
    with open(latest, newline="") as f:
        rows = list(csv.DictReader(f))
    return latest, rows

# ── Check live prices ─────────────────────────────────────────────────────────
def get_live_prices(symbols):
    """Get current bid/ask/last for a list of yf symbols."""
    if not symbols:
        return {}
    tickers = yf.Tickers(" ".join(symbols))
    prices  = {}
    for sym in symbols:
        try:
            info = tickers.tickers[sym].fast_info
            prices[sym] = float(info.last_price or 0)
        except:
            prices[sym] = 0.0
    return prices

# ── Main Executor ─────────────────────────────────────────────────────────────
def execute():
    today_str  = date.today().strftime("%Y-%m-%d")
    trades_out = os.path.join(SCRIPT_DIR, f"stock_strategy_trades_{today_str}.csv")

    # Try DB first (GitHub Actions / cloud), fall back to local CSV
    db_setups = load_setups_from_db()
    if db_setups is not None:
        setups   = db_setups
        log_file = None   # no CSV to update when running from DB
        print(f"[DB] Using {len(setups)} setups from Postgres")
    else:
        log_file, setups = load_latest_log()
        if not setups:
            return
        print(f"[CSV] Using {len(setups)} setups from local log file")

    pending = [s for s in setups if s.get("status") == "pending"]
    print(f"\nPending setups: {len(pending)}")

    if not pending:
        print("Nothing to execute.")
        return

    # Get live prices for all pending symbols
    symbols = [s['symbol'] for s in pending]
    print(f"Fetching live prices for {len(symbols)} symbols...")
    prices   = get_live_prices(symbols)

    triggered = []
    daily_risk_used = 0.0

    for setup in pending:
        sym    = setup['symbol']
        entry  = float(setup['entry'])
        stop   = float(setup['stop'])
        target = float(setup['target'])
        shares = int(setup['shares'])
        risk   = float(setup['risk_dollars'])

        live_price = prices.get(sym, 0)
        if live_price <= 0:
            continue

        # Check daily loss cap before each trade
        if daily_risk_used + risk > DAILY_LOSS_CAP:
            print(f"  DAILY LOSS CAP reached at ${daily_risk_used:.0f}. Stopping.")
            break

        # Entry trigger: price has broken above entry level (or is within 0.5% — late entry ok)
        late_entry_buffer = entry * 1.005   # accept up to 0.5% above entry
        if entry <= live_price <= late_entry_buffer:
            fill_price = live_price
        elif live_price > late_entry_buffer:
            # Too far extended — skip (price ran away)
            setup['status']  = 'skipped_extended'
            setup['notes']   = f'live={live_price:.2f} too far above entry={entry}'
            continue
        else:
            # Not triggered yet
            setup['status'] = 'waiting'
            continue

        # Trade is executable
        triggered.append({
            "symbol_tv":   setup['symbol_tv'],
            "symbol":      sym,
            "priority":    setup.get('priority', ''),
            "entry_tf":    setup.get('entry_tf', 'daily'),
            "htf_setup":   setup.get('htf_setup', ''),
            "combo":       setup['combo'],
            "shares":      shares,
            "entry":       entry,
            "fill_price":  round(fill_price, 4),
            "stop":        stop,
            "target":      target,
            "risk_dollars":risk,
            "action":      "BUY"
        })
        daily_risk_used   += risk
        setup['status']    = 'triggered'
        setup['filled_price'] = round(fill_price, 4)

    # Print execution list
    print(f"\n{'='*60}")
    print(f"  TRADES TO EXECUTE — {today_str}")
    print(f"  Total trades     : {len(triggered)}")
    print(f"  Total risk       : ${daily_risk_used:,.2f} / ${DAILY_LOSS_CAP:,.2f} cap")
    print(f"{'='*60}")
    for t in triggered:
        prio = t.get('priority', '')
        etf  = t.get('entry_tf', '')
        print(f"  BUY  {t['symbol']:<8} {t['shares']:>5} shares @ {t['fill_price']:<8.2f} "
              f"| Stop: {t['stop']:<8.2f} | Target: {t['target']:<8.2f} "
              f"| [{prio}] TF:{etf} {t['combo']}  | Risk: ${t['risk_dollars']:.0f}")
    print(f"{'='*60}\n")

    # Write trades CSV (Claude reads this to place orders)
    if triggered:
        with open(trades_out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=triggered[0].keys())
            writer.writeheader()
            writer.writerows(triggered)
        print(f"Trades saved to: {trades_out}")
        print("-> tv_place_orders.py will now submit these to TradingView paper trading.\n")

    # Update log file with new statuses (local only — skipped on GitHub Actions)
    if log_file and setups:
        with open(log_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=setups[0].keys())
            writer.writeheader()
            writer.writerows(setups)

    # ── Write to Postgres (dashboard) ──────────────────────────────────────
    if _db_upsert_trades and triggered:
        try:
            _db_upsert_trades(triggered)
        except Exception as _db_err:
            print(f"[DB] Warning: could not write trades to Postgres: {_db_err}")
    if _db_update_statuses and setups:
        try:
            _db_update_statuses(setups)
        except Exception as _db_err:
            print(f"[DB] Warning: could not update statuses in Postgres: {_db_err}")

    return trades_out, triggered

if __name__ == "__main__":
    execute()
