"""
Shared Postgres writer for The Strat bot scripts.
Requires: pip install psycopg2-binary python-dotenv
DATABASE_URL must be set in .env or environment.
"""

import os
from datetime import date
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
    print("[db_writer] psycopg2 not installed -- DB writes disabled. Run: pip install psycopg2-binary")


def _get_conn():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)


def _f(val, default=0.0) -> float:
    """Safe float conversion."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _b(val) -> bool:
    """Safe bool conversion from bool or string."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() == "true"
    return bool(val)


def clear_scan_date(scan_date: date) -> int:
    """
    Delete all ScanResult rows for the given date that are still 'pending'
    (i.e. not open/triggered trades). Called before each fresh scan write.
    Returns number of rows deleted.
    """
    if not _PSYCOPG2_AVAILABLE:
        return 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM "ScanResult"
               WHERE date = %s AND status = 'pending'""",
            (scan_date,)
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted:
            print(f"[db_writer] Cleared {deleted} pending rows for {scan_date}")
        return deleted
    except Exception as e:
        print(f"[db_writer] ERROR clearing scan date: {e}")
        return 0


def upsert_scan_results(setups: list[dict], scan_date: date | None = None) -> int:
    """
    Insert/update scan results for one day. Returns number of rows upserted.
    Clears existing pending rows for the scan date before inserting.

    v2.0 fields written:
      setupType, sequence, t1, t2, t3, rrT1, rrT2, rrT3, ftcQuarterly
    Legacy fields kept for backwards-compat:
      combo (= sequence), priority (= "FTC-3" / "FTC-2" / "FTC-1"),
      target (= t1), rr (= rrT1), ftcMonthly / ftcWeekly ("true"/"false")
    """
    if not _PSYCOPG2_AVAILABLE:
        return 0
    if not setups:
        return 0

    day = scan_date or date.today()
    clear_scan_date(day)

    rows = []
    for s in setups:
        sequence = s.get("sequence") or s.get("combo") or ""
        t1 = s.get("t1") or s.get("target")
        t2 = s.get("t2")
        t3 = s.get("t3")
        rr_t1 = _f(s.get("rr_t1") or s.get("rr"))
        rr_t2 = _f(s.get("rr_t2"))
        rr_t3 = _f(s.get("rr_t3"))

        last_close = s.get("last_close")

        rows.append((
            day,                                                 # date
            s.get("symbol", ""),                                 # symbol
            s.get("symbol_tv", s.get("symbol", "")),            # symbolTv
            sequence,                                            # combo
            s.get("priority", "FTC-3"),                         # priority
            str(s.get("ftc_monthly", "false")).lower(),          # ftcMonthly
            str(s.get("ftc_weekly", "false")).lower(),           # ftcWeekly
            None,                                                # tfChain (unused in v2)
            _f(s.get("entry")),                                  # entry
            _f(s.get("stop")),                                   # stop
            _f(t1),                                              # target (= T1)
            _f(s.get("risk_share")),                             # riskShare
            rr_t1,                                               # rr (= R:R T1)
            int(s.get("shares", 0)),                             # shares
            _f(s.get("risk_dollars")),                           # riskDollars
            _f(s.get("position_val")),                           # positionVal
            s.get("status", "pending"),                          # status
            None,                                                # fillPrice
            None,                                                # exitPrice
            None,                                                # pnl
            None,                                                # sector
            s.get("notes") or None,                             # notes
            # v2.0 new fields
            s.get("setup_type") or s.get("combo") or "",         # setupType
            sequence,                                            # sequence
            _f(last_close) if last_close is not None else None,  # lastClose
            _f(t1) if t1 is not None else None,                 # t1
            _f(t2) if t2 is not None else None,                 # t2
            _f(t3) if t3 is not None else None,                 # t3
            rr_t1 if rr_t1 else None,                           # rrT1
            rr_t2 if rr_t2 else None,                           # rrT2
            rr_t3 if rr_t3 else None,                           # rrT3
            _b(s.get("ftc_quarterly", True)),                    # ftcQuarterly
        ))

    sql = """
        INSERT INTO "ScanResult"
          (id, date, symbol, "symbolTv", combo, priority,
           "ftcMonthly", "ftcWeekly", "tfChain",
           entry, stop, target, "riskShare", rr, shares,
           "riskDollars", "positionVal", status,
           "fillPrice", "exitPrice", pnl, sector, notes, "createdAt",
           "setupType", sequence, "lastClose", t1, t2, t3, "rrT1", "rrT2", "rrT3", "ftcQuarterly")
        VALUES %s
        ON CONFLICT (date, symbol) DO UPDATE SET
          combo           = EXCLUDED.combo,
          priority        = EXCLUDED.priority,
          "tfChain"       = EXCLUDED."tfChain",
          entry           = EXCLUDED.entry,
          stop            = EXCLUDED.stop,
          target          = EXCLUDED.target,
          rr              = EXCLUDED.rr,
          shares          = EXCLUDED.shares,
          "riskDollars"   = EXCLUDED."riskDollars",
          status          = EXCLUDED.status,
          "setupType"     = EXCLUDED."setupType",
          sequence        = EXCLUDED.sequence,
          "lastClose"     = EXCLUDED."lastClose",
          t1              = EXCLUDED.t1,
          t2              = EXCLUDED.t2,
          t3              = EXCLUDED.t3,
          "rrT1"          = EXCLUDED."rrT1",
          "rrT2"          = EXCLUDED."rrT2",
          "rrT3"          = EXCLUDED."rrT3",
          "ftcQuarterly"  = EXCLUDED."ftcQuarterly"
    """

    template = """(
        gen_random_uuid()::text, %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s, %s, NOW(),
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
            symbol     = t.get("symbol", "")
            fill_price = _f(t.get("fill_price") or t.get("entry"))

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
                     (id, "scanResultId", "fillPrice", status, "orderPlaced",
                      "openedAt", "createdAt", "updatedAt")
                   VALUES (gen_random_uuid()::text, %s, %s, 'open', false,
                           NOW(), NOW(), NOW())
                   ON CONFLICT ("scanResultId") DO UPDATE SET
                     "fillPrice" = EXCLUDED."fillPrice",
                     "updatedAt" = NOW()
                """,
                (scan_result_id, fill_price)
            )

            # Update ScanResult status to triggered
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
