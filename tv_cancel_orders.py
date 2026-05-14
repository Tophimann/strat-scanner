"""
TradingView Paper Trading — EOD Order Canceller
================================================
Cancels all open/pending Buy Stop orders via the paper trading REST API.

Run this at EOD -1H (e.g. 21:00 CEST = 3:00 PM ET).
Rationale: The Strat combos are bar-specific. An untriggered Buy Stop loses
its signal validity once the next bar opens — cancel before EOD so a fresh
setup (if any) is placed clean next morning.

Requires TradingView Desktop running with --remote-debugging-port=9222

Usage:
    python tv_cancel_orders.py
"""

import json
import time
import threading
import requests
import websocket
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CDP_BASE = "http://localhost:9222"

# ── CDP: run JS in TradingView and return result ──────────────────────────────
def cdp_eval(expression):
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

    def on_open(ws):   ready.set()
    def on_message(ws, msg):
        data = json.loads(msg)
        if data.get("id") == msg_id:
            result_holder["data"] = data
            done.set()
    def on_error(ws, err):
        result_holder["error"] = str(err)
        done.set()

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


# ── Bootstrap paperFetch and get account ID ───────────────────────────────────
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


# ── Fetch all open/pending orders ─────────────────────────────────────────────
def get_open_orders(account_id):
    js = f"""
    (async function() {{
      const pf = window.__paperFetch;
      const r = await pf('trading/get_orders/{account_id}', {{ method: 'POST' }});
      return JSON.stringify(r);
    }})()
    """
    raw = cdp_eval(js)
    orders = json.loads(raw)
    if isinstance(orders, dict) and "d" in orders:
        orders = orders["d"]   # some endpoints wrap in {d: [...]}
    if not isinstance(orders, list):
        orders = []
    # Keep only pending/working orders (not filled, not cancelled)
    open_statuses = {"working", "inactive", "pending"}
    return [o for o in orders if str(o.get("status", "")).lower() in open_statuses]


# ── Cancel a single order ─────────────────────────────────────────────────────
def cancel_order(account_id, order_id):
    js = f"""
    (async function() {{
      const pf = window.__paperFetch;
      const r = await pf('trading/cancel/{account_id}', {{ body: {{ id: '{order_id}' }} }});
      return JSON.stringify(r);
    }})()
    """
    raw = cdp_eval(js)
    return json.loads(raw) if raw else {}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n{'='*60}")
    print(f"  TV Paper Trading -- EOD Order Canceller")
    print(f"  Run time    : {now}")
    print(f"  Reason      : Strat combos are bar-specific — cancel")
    print(f"                unfilled orders before new bar opens")
    print(f"{'='*60}")

    print("\n[1] Connecting to TradingView Desktop via CDP...")
    try:
        account_id, balance = get_account_id()
        print(f"  Account ID : {account_id}")
        print(f"  Balance    : ${balance:,.2f}")
    except Exception as e:
        print(f"  FATAL: {e}")
        print("  Make sure TradingView Desktop is running with --remote-debugging-port=9222")
        return 1

    print(f"\n[2] Fetching open orders...")
    try:
        open_orders = get_open_orders(account_id)
    except Exception as e:
        print(f"  ERROR fetching orders: {e}")
        return 1

    if not open_orders:
        print("  No open orders found — nothing to cancel.")
        print(f"{'='*60}\n")
        return 0

    print(f"  Found {len(open_orders)} open order(s):")
    for o in open_orders:
        sym    = o.get("symbol", "?")
        oid    = o.get("id", "?")
        status = o.get("status", "?")
        qty    = o.get("qty", "?")
        price  = o.get("price", "?")
        side   = o.get("side", "?")
        print(f"    {sym:<12} id={oid}  {side} {qty} @ {price}  [{status}]")

    print(f"\n[3] Cancelling {len(open_orders)} order(s)...")
    print(f"{'-'*60}")

    cancelled = 0
    for o in open_orders:
        oid = o.get("id", "?")
        sym = o.get("symbol", "?")
        print(f"  Cancel {sym:<12} id={oid} ... ", end="", flush=True)
        try:
            cancel_order(account_id, oid)
            print("OK")
            cancelled += 1
        except Exception as e:
            print(f"FAIL  {e}")
        time.sleep(0.2)

    print(f"{'-'*60}")
    print(f"\n  Cancelled : {cancelled}/{len(open_orders)}")
    print(f"{'='*60}\n")
    return 0 if cancelled == len(open_orders) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
