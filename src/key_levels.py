"""
Key Levels — The Strat v2.0
=============================
Computes the 6-level pivot hierarchy for target calculation.

Level order (ascending):
  PDH   - Prior Day High
  PWH   - Prior Week High
  PMH   - Prior Month High
  PQH   - Prior Quarter High
  WK52H - 52-Week High
  ATH   - All-Time High (within dataset)
"""

from datetime import date, timedelta
import pandas as pd
import numpy as np


# Level hierarchy for ordering targets
LEVEL_ORDER = ["pdh", "pwh", "pmh", "pqh", "wk52h", "ath"]


def _quarter_start_month(d: date) -> int:
    return ((d.month - 1) // 3) * 3 + 1


def calculate_key_levels(df_daily: pd.DataFrame, scan_date: date) -> dict:
    """
    Compute all 6 key levels as of scan_date.

    Uses ONLY bars with date < scan_date for PDH/PWH/PMH/PQH
    (prior period = not including scan_date itself).

    Parameters
    ----------
    df_daily  : Daily OHLCV DataFrame with DatetimeIndex
    scan_date : The completed trading day being evaluated

    Returns
    -------
    dict with keys: pdh, pwh, pmh, pqh, wk52h, ath  (float or None)
    """
    df = df_daily.copy()
    df.index = pd.to_datetime(df.index)

    # All bars strictly before scan_date
    hist = df[df.index.date < scan_date]
    if hist.empty:
        return {k: None for k in LEVEL_ORDER}

    # ── PDH: prior day high (last completed bar before scan_date) ────────────
    pdh = float(hist['High'].iloc[-1])

    # ── PWH: highest high of prior week ──────────────────────────────────────
    week_start = scan_date - timedelta(days=scan_date.weekday())  # Monday
    prior_week = hist[hist.index.date < week_start]
    pwh = float(prior_week['High'].max()) if not prior_week.empty else None

    # ── PMH: highest high of prior month ─────────────────────────────────────
    month_start = date(scan_date.year, scan_date.month, 1)
    prior_month = hist[hist.index.date < month_start]
    pmh = float(prior_month['High'].max()) if not prior_month.empty else None

    # ── PQH: highest high of prior quarter ───────────────────────────────────
    q_month = _quarter_start_month(scan_date)
    q_start = date(scan_date.year, q_month, 1)
    prior_quarter = hist[hist.index.date < q_start]
    pqh = float(prior_quarter['High'].max()) if not prior_quarter.empty else None

    # ── 52WH: highest high in last 52 weeks (rolling) ────────────────────────
    cutoff_52w = scan_date - timedelta(weeks=52)
    window_52w = hist[hist.index.date >= cutoff_52w]
    wk52h = float(window_52w['High'].max()) if not window_52w.empty else None

    # ── ATH: all-time high in dataset ─────────────────────────────────────────
    ath = float(hist['High'].max())

    return {
        "pdh":   pdh,
        "pwh":   pwh,
        "pmh":   pmh,
        "pqh":   pqh,
        "wk52h": wk52h,
        "ath":   ath,
    }


def get_targets(entry: float, levels: dict, n: int = 3) -> list:
    """
    Return the next n key levels strictly above entry, in ascending order.
    Skips None values and levels <= entry.

    Parameters
    ----------
    entry  : Entry price (Buy Stop level)
    levels : dict from calculate_key_levels()
    n      : How many targets to return (default 3)

    Returns
    -------
    List of up to n floats: [T1, T2, T3, ...]
    """
    candidates = []
    for key in LEVEL_ORDER:
        val = levels.get(key)
        if val is not None and not np.isnan(val) and val > entry:
            candidates.append(val)

    # Deduplicate and sort ascending
    candidates = sorted(set(round(v, 4) for v in candidates))
    return candidates[:n]


def calc_rr(entry: float, stop: float, target: float) -> float:
    """R:R = (target - entry) / (entry - stop). Returns 0.0 if invalid."""
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return round((target - entry) / risk, 2)


# ── Unit Tests ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import numpy as np

    idx = pd.date_range("2023-01-02", periods=600, freq="B")
    np.random.seed(42)
    price = 100 + np.cumsum(np.random.randn(len(idx)) * 0.5)
    price = np.maximum(price, 10)  # floor at 10
    df_test = pd.DataFrame({
        "Open":   price - 0.3,
        "High":   price + 1.0,
        "Low":    price - 1.0,
        "Close":  price,
        "Volume": 1_000_000,
    }, index=idx)

    scan_d = idx[-1].date()
    levels = calculate_key_levels(df_test, scan_d)

    print(f"Scan date : {scan_d}")
    print(f"PDH       : {levels['pdh']:.2f}")
    print(f"PWH       : {levels['pwh']:.2f}" if levels['pwh'] else "PWH       : None")
    print(f"PMH       : {levels['pmh']:.2f}" if levels['pmh'] else "PMH       : None")
    print(f"PQH       : {levels['pqh']:.2f}" if levels['pqh'] else "PQH       : None")
    print(f"52WH      : {levels['wk52h']:.2f}" if levels['wk52h'] else "52WH      : None")
    print(f"ATH       : {levels['ath']:.2f}")

    # get_targets test
    entry = levels['pdh'] - 0.50  # just below PDH
    targets = get_targets(entry, levels)
    print(f"\nEntry     : {entry:.2f}")
    print(f"Targets   : {[round(t,2) for t in targets]}")

    assert len(targets) >= 1, "Should find at least 1 target!"
    assert all(t > entry for t in targets), "All targets must be above entry!"
    assert targets == sorted(targets), "Targets must be ascending!"
    print("\n[PASS] key_levels tests passed OK")
