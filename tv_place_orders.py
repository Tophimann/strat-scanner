"""
TradingView Paper Trading — REST Order Placer
=============================================
Reads stock_strategy_trades_YYYY-MM-DD.csv and places Buy Stop orders
directly through the paper trading API via TradingView Desktop's CDP.

Endpoint: POST https://papertrading.tradingview.com/trading/place/{accountId}
Auth:     Session cookies (credentials: include) — extracted via CDP eval

No UI automation. Requires TradingView Desktop running with:
  --remote-debugging-port=9222

Usage:
    python tv_place_orders.py [YYYY-MM-DD]   # defaults to today
"""

import sys
import json
import csv
import os
import time
import threading
import requests
import websocket
from datetime import date
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CDP_BASE   = "http://localhost:9222"
TV_BASE    = "https://papertrading.tradingview.com"


# ── Load trades from Postgres (primary) ──────────────────────────────────────
def load_trades_from_db():
    """Return (list_of_trade_dicts, conn) for today's unplaced open trades,
    or (None, None) on failure. Caller must close conn after marking orders placed."""
    try:
        import psycopg2
        from dotenv import load_dotenv
        for _p in [str(SCRIPT_DIR / ".env"), str(SCRIPT_DIR / "dashboard" / ".env")]:
            if os.path.exists(_p):
                load_dotenv(_p, override=False)
                break
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return None, None
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute("""
            SELECT t.id, t."fillPrice", sr."symbolTv", sr.symbol, sr.shares, sr.stop, sr.target
            FROM "Trade" t
            JOIN "ScanResult" sr ON sr.id = t."scanResultId"
            WHERE t.status = 'open'
              AND t."openedAt" >= CURRENT_DATE
              AND t."orderPlaced" = false
        """)
        rows = []
        for trade_id, fill_price, symbol_tv, symbol, shares, stop, target in cur.fetchall():
            rows.append({
                "_trade_id":  trade_id,
                "symbol_tv":  symbol_tv,
                "symbol":     symbol,
                "shares":     str(int(shares)),
                "fill_price": str(fill_price),
                "stop":       str(stop),
                "target":     str(target),
            })
        cur.close()
        print(f"[DB] Loaded {len(rows)} unplaced trades from Postgres")
        return rows, conn
    except Exception as e:
        print(f"[DB] Could not load trades from Postgres: {e}")
        return None, None


def mark_order_placed(conn, trade_id):
    """Set orderPlaced=true on a Trade row after successful order submission."""
    try:
        cur = conn.cursor()
        cur.execute('UPDATE "Trade" SET "orderPlaced"=true, "updatedAt"=NOW() WHERE id=%s', (trade_id,))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] Warning: could not mark trade {trade_id} as placed: {e}")

# ── CDP: run JS in TradingView and return result ──────────────────────────────
def cdp_eval(expression):
    """Evaluate JS in the TradingView renderer via CDP. Returns the value."""
    targets = requests.get(f"{CDP_BASE}/json", timeout=5).json()
    page = next(
        (t for t in targets if t.get("type") == "page" and "tradingview" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None)
    )
    if not page:
        raise RuntimeError("No TradingView page target found in CDP")

    result_holder = {}
    ready = threading.Event()
    done  = threading.Event()
    msg_id = 1

    def on_open(ws):
        ready.set()

    def on_message(ws, msg):
        data = json.loads(msg)
        if data.get("id") == msg_id:
            result_holder["data"] = data
            done.set()

    def on_error(ws, err):
        result_holder["error"] = str(err)
        done.set()

    # Use empty Origin to bypass the --remote-allow-origins check
    ws_app = websocket.WebSocketApp(
        page["webSocketDebuggerUrl"],
        on_open=on_open, on_message=on_message, on_error=on_error,
        header={"Origin": ""}
    )
    t = threading.Thread(target=lambda: ws_app.run_forever(), daemon=True)
    t.start()
    ready.wait(timeout=5)

    ws_app.send(json.dumps({
        "id": msg_id,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True, "awaitPromise": True}
    }))
    done.wait(timeout=15)
    ws_app.close()

    if "error" in result_holder:
        raise RuntimeError(f"CDP error: {result_holder['error']}")

    res = result_holder.get("data", {}).get("result", {}).get("result", {})
    if res.get("type") == "object" and res.get("subtype") == "error":
        raise RuntimeError(f"JS error: {res.get('description')}")
    return res.get("value")


# ── Get paper trading account ID ──────────────────────────────────────────────
def get_account_id():
    js = """
    (async function() {
      const req = window.__wpRequire || (() => {
        let r = null;
        window.webpackChunktradingview.push([[Symbol()], {}, function(req) { r = req; }]);
        return r;
      })();
      if (!window.__wpRequire) window.__wpRequire = req;
      const pf = req('83950').paperFetch;
      window.__paperFetch = pf;
      const accounts = await pf('trading/accounts', { method: 'POST' });
      const acc = Array.isArray(accounts) ? accounts[0] : accounts;
      return JSON.stringify({ accountId: acc.accountId, balance: acc.balance });
    })()
    """
    raw = cdp_eval(js)
    data = json.loads(raw)
    return data["accountId"], data["balance"]


# ── Place a single Buy Stop order ─────────────────────────────────────────────
def place_order(account_id, symbol, qty, stop_price, sl, tp):
    js = f"""
    (async function() {{
      const pf = window.__paperFetch;
      const body = {{
        symbol: '{symbol}',
        type: 'stop',
        qty: {qty},
        side: 'buy',
        price: {stop_price},
        sl: {sl},
        tp: {tp}
      }};
      const r = await pf('trading/place/{account_id}', {{ body }});
      return JSON.stringify({{ id: r.id, status: r.status, type: r.type, price: r.price }});
    }})()
    """
    raw = cdp_eval(js)
    return json.loads(raw)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  TV Paper Trading -- REST Order Placer")
    print(f"  Date : {target_date}")
    print(f"{'='*60}")

    # Primary: load from Postgres DB
    db_conn = None
    trades, db_conn = load_trades_from_db()

    # Fallback: local CSV
    if trades is None:
        trades_file = SCRIPT_DIR / f"stock_strategy_trades_{target_date}.csv"
        print(f"  [CSV fallback] {trades_file.name}")
        if not trades_file.exists():
            print(f"  ERROR: {trades_file} not found. Run morning executor first.")
            return 1
        with open(trades_file, newline="", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))

    if not trades:
        print("  No unplaced trades found -- nothing to do.")
        if db_conn:
            db_conn.close()
        return 0

    print(f"  Trades to place: {len(trades)}")

    print("\n[1] Connecting to TradingView Desktop via CDP...")
    try:
        account_id, balance = get_account_id()
        print(f"  Account ID : {account_id}")
        print(f"  Balance    : ${balance:,.2f}")
    except Exception as e:
        print(f"  FATAL: {e}")
        print("  Make sure TradingView Desktop is running with --remote-debugging-port=9222")
        if db_conn:
            db_conn.close()
        return 1

    print(f"\n[2] Placing {len(trades)} Buy Stop order(s) via REST API...")
    print(f"{'-'*60}")

    ok_count = 0
    for trade in trades:
        sym       = trade.get("symbol_tv", trade.get("symbol", "?"))
        yf_sym    = trade.get("symbol", sym.split(":")[-1])
        qty       = int(trade["shares"])
        stop_px   = float(trade["fill_price"])
        sl_px     = float(trade["stop"])
        tp_px     = float(trade["target"])
        trade_id  = trade.get("_trade_id")

        print(f"  {sym:<14} {qty:>5} shares  stop@${stop_px:<8}  SL ${sl_px:<8}  TP ${tp_px:<8} ... ", end="", flush=True)

        try:
            result = place_order(account_id, yf_sym, qty, stop_px, sl_px, tp_px)
            print(f"OK  id={result['id']}  status={result['status']}")
            ok_count += 1
            # Mark order as placed in DB so it won't be re-submitted
            if db_conn and trade_id:
                mark_order_placed(db_conn, trade_id)
        except Exception as e:
            print(f"FAIL  {e}")

        time.sleep(0.3)

    if db_conn:
        db_conn.close()

    print(f"{'-'*60}")
    print(f"\n  Placed : {ok_count}/{len(trades)}")
    print(f"{'='*60}\n")
    return 0 if ok_count == len(trades) else 1


if __name__ == "__main__":
    sys.exit(main())
