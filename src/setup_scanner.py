"""
Setup Scanner — The Strat v2.0
================================
Detects all 5 setup patterns on the daily chart.

Setup Catalog (all Long, all FTC-bullish):
  2-1-2  : bars[-2]=2U, bars[-1]=1  (Inside) -> entry above inside bar high
  3-1-2  : bars[-2]=3,  bars[-1]=1  (Inside) -> entry above inside bar high
  1-2-2  : bars[-2]=1,  bars[-1]=2U          -> entry above 2U high
  3-2-2  : bars[-2]=3,  bars[-1]=2U          -> entry above 2U high
  Machine Gun : price already broke a pivot, entry = next pivot above

Signal candle = bars[-1] (last completed bar).
Stop          = Low of signal candle.
Entry         = High of signal candle + 0.01 (Buy Stop, 1 cent above high).
"""

from datetime import date
import pandas as pd

from bar_classifier import classify_bar
from key_levels import calculate_key_levels, get_targets, calc_rr, LEVEL_ORDER


# ── 3-Bar Combo Patterns ──────────────────────────────────────────────────────
# (bars[-2] type, bars[-1] type, setup name)
#
# "2-1-2" has TWO bullish variants:
#   - 2U-1: prior bar was directional UP   → continuation setup
#   - 2D-1: prior bar was directional DOWN → REVERSAL setup  (e.g. 2D-1-2U)
# Both produce the same setup name; the sequence field ("2U-1" vs "2D-1")
# tells the trader which variant it is.
THREE_BAR_COMBOS = [
    ("2U", "1",  "2-1-2"),   # continuation: up, inside, potential up
    ("2D", "1",  "2-1-2"),   # reversal:     down, inside, potential up
    ("3",  "1",  "3-1-2"),   # outside bar, inside, potential up
    ("1",  "2U", "1-2-2"),   # inside then up, potential continuation
    ("3",  "2U", "3-2-2"),   # outside then up, potential continuation
]

TICK = 0.01  # Buy Stop = High + 1 cent


def _classify_last_bars(df: pd.DataFrame, scan_date: date):
    """
    Extract and classify the last 2 completed bars.

    Returns (b2_type, b1_type, signal_bar_row) or (None, None, None).
    Requires at least 4 rows: bars[-4] as context for classifying bars[-3], etc.

    bars[-1] = signal candle (last completed, dated scan_date or prior)
    bars[-2] = the bar before signal
    """
    # Only use bars up to and including scan_date
    df = df[df.index.date <= scan_date].copy()
    df = df.dropna(subset=["High", "Low", "Open", "Close"])

    if len(df) < 4:
        return None, None, None

    # Classify bars[-2] relative to bars[-3]
    b2_type = classify_bar(
        float(df.iloc[-2]["High"]), float(df.iloc[-2]["Low"]),
        float(df.iloc[-3]["High"]), float(df.iloc[-3]["Low"]),
    )
    # Classify bars[-1] (signal) relative to bars[-2]
    b1_type = classify_bar(
        float(df.iloc[-1]["High"]), float(df.iloc[-1]["Low"]),
        float(df.iloc[-2]["High"]), float(df.iloc[-2]["Low"]),
    )

    return b2_type, b1_type, df.iloc[-1]


def detect_3bar_setups(df: pd.DataFrame, scan_date: date,
                       key_levels: dict, ftc: dict) -> list:
    """
    Check for 2-1-2, 3-1-2, 1-2-2, 3-2-2 setups.
    Returns list of setup dicts (usually 0 or 1 per ticker).
    """
    b2_type, b1_type, signal = _classify_last_bars(df, scan_date)
    if b2_type is None:
        return []

    results = []
    for (expected_prev, expected_sig, setup_name) in THREE_BAR_COMBOS:
        if b2_type == expected_prev and b1_type == expected_sig:
            signal_high = float(signal["High"])
            signal_low  = float(signal["Low"])
            entry = round(signal_high + TICK, 2)
            stop  = round(signal_low, 2)

            if stop >= entry:
                continue  # degenerate bar, skip

            targets = get_targets(entry, key_levels)
            if not targets:
                continue  # no target levels above entry

            t1 = targets[0] if len(targets) > 0 else None
            t2 = targets[1] if len(targets) > 1 else None
            t3 = targets[2] if len(targets) > 2 else None

            rr_t1 = calc_rr(entry, stop, t1) if t1 else 0.0
            rr_t2 = calc_rr(entry, stop, t2) if t2 else 0.0
            rr_t3 = calc_rr(entry, stop, t3) if t3 else 0.0

            sequence = f"{b2_type}-{b1_type}"

            results.append(_build_setup(
                setup_type=setup_name,
                sequence=sequence,
                entry=entry,
                stop=stop,
                t1=t1, t2=t2, t3=t3,
                rr_t1=rr_t1, rr_t2=rr_t2, rr_t3=rr_t3,
                ftc=ftc,
                signal_date=df[df.index.date <= scan_date].iloc[-1].name.date(),
            ))

    return results


def detect_machine_gun(df: pd.DataFrame, scan_date: date,
                       key_levels: dict, ftc: dict) -> list:
    """
    Pivot Machine Gun: signal candle (bars[-1]) already broke a pivot level.
    Entry = next pivot above current price (Buy Stop).
    Stop  = Low of signal candle.

    Fires when bars[-1].High broke exactly one pivot in the hierarchy
    and the NEXT pivot is not yet broken.
    """
    df_cut = df[df.index.date <= scan_date].dropna(subset=["High", "Low"])
    if df_cut.empty:
        return []

    signal     = df_cut.iloc[-1]
    signal_high = float(signal["High"])
    signal_low  = float(signal["Low"])

    # Build ordered list of (level_name, level_value), skip None
    hierarchy = [
        (k, key_levels.get(k))
        for k in LEVEL_ORDER
        if key_levels.get(k) is not None
    ]

    for i, (name, level) in enumerate(hierarchy):
        # Signal high crossed this level but NOT the next one
        if signal_high > level:
            # Find the next unbroken level
            next_levels = [(n2, v2) for (n2, v2) in hierarchy[i+1:]
                           if v2 > signal_high]
            if not next_levels:
                break  # above all levels, nothing to target

            next_name, next_level = next_levels[0]
            entry = round(next_level + TICK, 2)
            stop  = round(signal_low, 2)

            if stop >= entry:
                break

            # Targets are the levels above entry
            targets = get_targets(entry, key_levels)
            if not targets:
                break

            t1 = targets[0] if len(targets) > 0 else None
            t2 = targets[1] if len(targets) > 1 else None
            t3 = targets[2] if len(targets) > 2 else None

            return [_build_setup(
                setup_type="machine-gun",
                sequence=f">{name.upper()}",
                entry=entry,
                stop=stop,
                t1=t1, t2=t2, t3=t3,
                rr_t1=calc_rr(entry, stop, t1) if t1 else 0.0,
                rr_t2=calc_rr(entry, stop, t2) if t2 else 0.0,
                rr_t3=calc_rr(entry, stop, t3) if t3 else 0.0,
                ftc=ftc,
                signal_date=signal.name.date(),
            )]

    return []


def scan_ticker(df: pd.DataFrame, scan_date: date, ftc: dict) -> list:
    """
    Run all setup detectors on one ticker.
    Returns list of setup dicts (may be empty).
    """
    key_levels = calculate_key_levels(df, scan_date)
    setups = detect_3bar_setups(df, scan_date, key_levels, ftc)
    setups += detect_machine_gun(df, scan_date, key_levels, ftc)
    return setups


# ── Internal builder ─────────────────────────────────────────────────────────
def _build_setup(setup_type, sequence, entry, stop,
                 t1, t2, t3, rr_t1, rr_t2, rr_t3,
                 ftc, signal_date) -> dict:
    return {
        "setup_type":  setup_type,
        "sequence":    sequence,
        "entry":       entry,
        "stop":        stop,
        "t1":          t1,
        "t2":          t2,
        "t3":          t3,
        "rr_t1":       rr_t1,
        "rr_t2":       rr_t2,
        "rr_t3":       rr_t3,
        "ftc_levels":  ftc.get("ftc_levels", 0),
        "ftc_q":       ftc.get("q_bull", False),
        "ftc_m":       ftc.get("m_bull", False),
        "ftc_w":       ftc.get("w_bull", False),
        "signal_date": str(signal_date),
    }


# ── Unit Tests ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import numpy as np

    def _make_bar(prev_high, prev_low, bar_type):
        """Create a bar of the given type relative to (prev_high, prev_low)."""
        if bar_type == "2U":
            return prev_high + 1.0, prev_low + 0.2  # new high, same low band
        elif bar_type == "1":
            return prev_high - 0.5, prev_low + 0.5  # inside
        elif bar_type == "2D":
            return prev_high - 0.2, prev_low - 1.0
        elif bar_type == "3":
            return prev_high + 1.0, prev_low - 1.0
        return prev_high, prev_low

    # Build a synthetic daily series
    base_high, base_low = 100.0, 95.0
    rows = []
    for _ in range(10):
        rows.append({"High": base_high, "Low": base_low,
                     "Open": base_low + 0.5, "Close": base_high - 0.5,
                     "Volume": 1_000_000})
        base_high += 0.5; base_low += 0.5

    # Add bars[-2]=2U and bars[-1]=1 to make a 2-1-2 setup
    h2u, l2u = _make_bar(rows[-1]["High"], rows[-1]["Low"], "2U")
    rows.append({"High": h2u, "Low": l2u, "Open": l2u+0.2, "Close": h2u-0.2, "Volume": 1_000_000})
    h1, l1 = _make_bar(h2u, l2u, "1")
    rows.append({"High": h1, "Low": l1, "Open": l1+0.1, "Close": h1-0.1, "Volume": 1_000_000})

    idx = pd.date_range("2025-01-02", periods=len(rows), freq="B")
    df_test = pd.DataFrame(rows, index=idx)
    scan_d  = idx[-1].date()

    mock_ftc = {"ftc_levels": 3, "q_bull": True, "m_bull": True, "w_bull": True, "ftc_ok": True}
    setups = scan_ticker(df_test, scan_d, mock_ftc)

    print(f"Setups found: {len(setups)}")
    for s in setups:
        print(f"  {s['setup_type']:12s}  seq={s['sequence']}  "
              f"entry={s['entry']:.2f}  stop={s['stop']:.2f}  "
              f"T1={s['t1']}  R:R T1={s['rr_t1']}")

    # Should find a 2-1-2 setup
    types = [s["setup_type"] for s in setups]
    assert "2-1-2" in types, f"Expected 2-1-2 setup, got: {types}"
    print("\n[PASS] setup_scanner tests passed OK")
