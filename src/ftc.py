"""
Full Timeframe Continuity (FTC) — The Strat v2.0
==================================================
Gate 1: last close must be above the open of each running period.

  Quarterly Open : first trading day of Jan / Apr / Jul / Oct
  Monthly Open   : first trading day of current month
  Weekly Open    : Monday (or first trading day) of current week

FTC is bullish only when ALL THREE conditions are met.
No FTC -> no setup, no trade.
"""

from datetime import date, timedelta
import pandas as pd


# ── Helper: quarter start month ───────────────────────────────────────────────
def _quarter_start_month(d: date) -> int:
    """Return the first month of the quarter containing date d (1, 4, 7, or 10)."""
    return ((d.month - 1) // 3) * 3 + 1


# ── FTC Calculation ────────────────────────────────────────────────────────────
def get_ftc(df_daily: pd.DataFrame, scan_date: date) -> dict:
    """
    Compute FTC levels for scan_date using daily OHLCV history.

    Parameters
    ----------
    df_daily  : Daily OHLCV DataFrame with DatetimeIndex (2+ years recommended)
    scan_date : The completed trading day being evaluated

    Returns
    -------
    dict with keys:
      quarterly_open, monthly_open, weekly_open  – float, the period opens
      q_bull, m_bull, w_bull                     – bool, is last_close > period_open?
      ftc_ok                                     – bool, all 3 levels bullish
      ftc_levels                                 – int 0-3, how many are bullish
      last_close                                 – float
    """
    df = df_daily.copy()
    df.index = pd.to_datetime(df.index)

    # Last close = close of scan_date bar
    scan_rows = df[df.index.date == scan_date]
    if scan_rows.empty:
        # Fall back to last available row
        scan_rows = df[df.index.date <= scan_date].tail(1)
    if scan_rows.empty:
        return _empty_ftc()
    last_close = float(scan_rows['Close'].iloc[-1])

    # ── Quarterly Open ────────────────────────────────────────────────────────
    q_month = _quarter_start_month(scan_date)
    q_start = date(scan_date.year, q_month, 1)
    q_rows = df[df.index.date >= q_start]
    if q_rows.empty:
        return _empty_ftc()
    quarterly_open = float(q_rows['Open'].iloc[0])

    # ── Monthly Open ──────────────────────────────────────────────────────────
    m_start = date(scan_date.year, scan_date.month, 1)
    m_rows = df[df.index.date >= m_start]
    if m_rows.empty:
        return _empty_ftc()
    monthly_open = float(m_rows['Open'].iloc[0])

    # ── Weekly Open ───────────────────────────────────────────────────────────
    # Monday of current week (weekday 0 = Monday)
    w_start = scan_date - timedelta(days=scan_date.weekday())
    w_rows = df[df.index.date >= w_start]
    if w_rows.empty:
        return _empty_ftc()
    weekly_open = float(w_rows['Open'].iloc[0])

    # ── Compare ───────────────────────────────────────────────────────────────
    q_bull = last_close > quarterly_open
    m_bull = last_close > monthly_open
    w_bull = last_close > weekly_open

    return {
        "quarterly_open": quarterly_open,
        "monthly_open":   monthly_open,
        "weekly_open":    weekly_open,
        "last_close":     last_close,
        "q_bull":         q_bull,
        "m_bull":         m_bull,
        "w_bull":         w_bull,
        "ftc_ok":         q_bull and m_bull and w_bull,
        "ftc_levels":     int(q_bull) + int(m_bull) + int(w_bull),
    }


def _empty_ftc() -> dict:
    return {
        "quarterly_open": None, "monthly_open": None, "weekly_open": None,
        "last_close": None, "q_bull": False, "m_bull": False, "w_bull": False,
        "ftc_ok": False, "ftc_levels": 0,
    }


# ── Unit Tests ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import numpy as np

    # Build a synthetic 2-year daily DataFrame
    # Scenario: price trending up, all FTC levels should be bullish
    idx = pd.date_range("2024-01-02", periods=400, freq="B")  # business days
    closes = 100.0 + np.linspace(0, 50, len(idx))             # steadily rising
    opens  = closes - 0.5
    df_test = pd.DataFrame({
        "Open":   opens,
        "High":   closes + 1,
        "Low":    opens  - 1,
        "Close":  closes,
        "Volume": 1_000_000,
    }, index=idx)

    scan_d = idx[-1].date()
    result = get_ftc(df_test, scan_d)
    print(f"Scan date : {scan_d}")
    print(f"Last close: {result['last_close']:.2f}")
    print(f"Q open    : {result['quarterly_open']:.2f}  -> Q bull: {result['q_bull']}")
    print(f"M open    : {result['monthly_open']:.2f}    -> M bull: {result['m_bull']}")
    print(f"W open    : {result['weekly_open']:.2f}     -> W bull: {result['w_bull']}")
    print(f"FTC ok    : {result['ftc_ok']}  (levels: {result['ftc_levels']}/3)")

    assert result["ftc_ok"], "FTC should be bullish on uptrend!"
    print("\n[PASS] Bullish uptrend -> FTC ok")

    # Scenario: price crashed below all period opens -> FTC bearish
    closes_bear = 100.0 + np.linspace(0, -30, len(idx))
    opens_bear  = closes_bear + 0.5
    df_bear = pd.DataFrame({
        "Open":   opens_bear,
        "High":   opens_bear + 1,
        "Low":    closes_bear - 1,
        "Close":  closes_bear,
        "Volume": 1_000_000,
    }, index=idx)

    result_bear = get_ftc(df_bear, scan_d)
    assert not result_bear["ftc_ok"], "FTC should be bearish on downtrend!"
    print("[PASS] Bearish downtrend -> FTC not ok")

    print("\nAll FTC tests passed OK")
