"""
Shared Postgres writer for The Strat bot scripts.
Requires: pip install psycopg2-binary python-dotenv
DATABASE_URL must be set in .env or environment.
"""

import os
import re
from datetime import date, datetime
from dotenv import load_dotenv

# Load DATABASE_URL: env var takes priority (GitHub Actions / Railway),
# then .env in the script dir, then dashboard/.env for local dev.
_script_dir = os.path.dirname(os.path.abspath(__file__))
for _env_path in [
    os.path.join(_script_dir, ".env"),
    os.path.join(_script_dir, "dashboard", ".env"),
]:
    if os.path.exists(_env_path):
        load_dotenv(dotenv_path=_env_path, override=False)
        break

try:
    import psycopg2
    from psycopg2.extras import execute_values
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False
    print("[db_writer] psycopg2 not installed — DB writes disabled. Run: pip install psycopg2-binary")


def _get_conn():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)


def _priority_key(setup: dict) -> str:
    """Normalise priority field to DB-expected values."""
    raw = setup.get("priority", "P3-ALIGNED")
    if raw in ("P1-TRIPLE", "P1-DOUBLE", "P3-ALIGNED"):
        return raw
    if raw.startswith("P2"):
        return "P2-x-COIL"
    return raw


def _tf_chain(setup: dict) -> str | None:
    """Build a human-readable TF chain string from scanner fields."""
    htf = setup.get("htf_setup", "")
    entry_tf = setup.get("entry_tf", "")
    if htf and entry_tf:
        return f"{htf}→{entry_tf}"
    return entry_tf or htf or None


def upsert_scan_results(setups: list[dict], scan_date: date | None = None) -> int:
    """Insert/update scan results for one day. Returns number of rows upserted."""
    if not _PSYCOPG2_AVAILABLE:
        return 0
    if not setups:
        return 0

    day = scan_date or date.today()

    rows = []
    for s in setups:
        rows.append((
            day,
            s.get("symbol", ""),
            s.get("symbol_tv", s.get("symbol", "")),
            s.get("combo", ""),
            _priority_key(s),
            s.get("ftc_monthly", ""),
            s.get("ftc_weekly", ""),
            _tf_chain(s),
            float(s.get("entry", 0)),
            float(s.get("stop", 0)),
            float(s.get("target", 0)),
            float(s.get("risk_share", 0)),
            float(s.get("rr", 0)),
            int(s.get("shares", 0)),
            float(s.get("risk_dollars", 0)),
            float(s.get("position_val", 0)),
            s.get("status", "pending"),
            None,  # fillPrice
            None,  # exitPrice
            None,  # pnl
            None,  # sector (fetched later by dashboard)
            s.get("notes", None),
        ))

    sql = """
        INSERT INTO "ScanResult"
          (id, date, symbol, "symbolTv", combo, priority,
           "ftcMonthly", "ftcWeekly", "tfChain",
           entry, stop, target, "riskShare", rr, shares,
           "riskDollars", "positionVal", status,
           "fillPrice", "exitPrice", pnl, sector, notes, "createdAt")
        VALUES %s
        ON CONFLICT (date, symbol) DO UPDATE SET
          combo        = EXCLUDED.combo,
          priority     = EXCLUDED.priority,
          "tfChain"    = EXCLUDED."tfChain",
          entry        = EXCLUDED.entry,
          stop         = EXCLUDED.stop,
          target       = EXCLUDED.target,
          rr           = EXCLUDED.rr,
          shares       = EXCLUDED.shares,
          "riskDollars"= EXCLUDED."riskDollars",
          status       = EXCLUDED.status
    """

    template = """(
        gen_random_uuid()::text, %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s, %s, NOW()
    )"""

    try:
        conn = _get_conn()
        cur = conn.cursor()
        execute_values(cur, sql, rows, template=template)
        conn.commit()
        cur.close()
        conn.close()
        print(f"[db_writer] Upserted {len(rows)} scan results to DB")
        return len(rows)
    except Exception as e:
        print(f"[db_writer] ERROR upserting scan results: {e}")
        return 0


def upsert_trades(trades: list[dict]) -> int:
    """Insert/update triggered trades. Returns number upserted."""
    if not _PSYCOPG2_AVAILABLE:
        return 0
    if not trades:
        return 0

    try:
        conn = _get_conn()
        cur = conn.cursor()

        for t in trades:
            symbol = t.get("symbol", "")
            fill_price = float(t.get("fill_price", t.get("entry", 0)))
            stop = float(t.get("stop", 0))
            risk_dollars = float(t.get("risk_dollars", 988.52))
            pnl_r = None  # not known at entry

            # Lookup ScanResult id by symbol + most recent date
            cur.execute(
                """SELECT id FROM "ScanResult"
                   WHERE symbol = %s
                   ORDER BY date DESC LIMIT 1""",
                (symbol,)
            )
            row = cur.fetchone()
            if not row:
                print(f"[db_writer] No ScanResult found for {symbol}, skipping trade insert")
                continue
            scan_result_id = row[0]

            # Upsert Trade
            cur.execute(
                """INSERT INTO "Trade"
                     (id, "scanResultId", "fillPrice", status, "orderPlaced", "openedAt", "createdAt", "updatedAt")
                   VALUES (gen_random_uuid()::text, %s, %s, 'open', false, NOW(), NOW(), NOW())
                   ON CONFLICT ("scanResultId") DO UPDATE SET
                     "fillPrice" = EXCLUDED."fillPrice",
                     "updatedAt" = NOW()
                """,
                (scan_result_id, fill_price)
            )

            # Also update ScanResult status → triggered
            cur.execute(
                """UPDATE "ScanResult" SET status='triggered', "fillPrice"=%s
                   WHERE id=%s""",
                (fill_price, scan_result_id)
            )

        conn.commit()
        cur.close()
        conn.close()
        print(f"[db_writer] Upserted {len(trades)} trades to DB")
        return len(trades)
    except Exception as e:
        print(f"[db_writer] ERROR upserting trades: {e}")
        return 0


def update_scan_statuses(setups: list[dict]) -> int:
    """Update status fields on existing ScanResult rows after executor runs."""
    if not _PSYCOPG2_AVAILABLE:
        return 0
    if not setups:
        return 0

    try:
        conn = _get_conn()
        cur = conn.cursor()
        updated = 0
        for s in setups:
            symbol = s.get("symbol", "")
            status = s.get("status", "pending")
            cur.execute(
                """UPDATE "ScanResult" SET status=%s
                   WHERE symbol=%s
                   ORDER BY date DESC
                   LIMIT 1""",
                (status, symbol)
            )
            updated += cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        print(f"[db_writer] Updated {updated} ScanResult statuses")
        return updated
    except Exception as e:
        print(f"[db_writer] ERROR updating statuses: {e}")
        return 0
