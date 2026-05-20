"""
Bar Type Classifier — The Strat v2.0
======================================
Classifies each daily candle relative to the prior candle.

Types:
  1   – Inside Bar:       High <= Prev High  AND  Low >= Prev Low
  2U  – Directional Up:   High > Prev High   AND  Low >= Prev Low
  2D  – Directional Down: Low  < Prev Low    AND  High <= Prev High
  3   – Outside Bar:      High > Prev High   AND  Low  < Prev Low
"""


def classify_bar(high: float, low: float, prev_high: float, prev_low: float) -> str:
    """Return bar type: '1', '2U', '2D', or '3'."""
    new_high = high > prev_high
    new_low  = low  < prev_low
    if new_high and new_low:
        return "3"
    elif new_high:
        return "2U"
    elif new_low:
        return "2D"
    else:
        return "1"


# ── Unit Tests ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        # (high, low, prev_high, prev_low, expected)
        (100, 90, 105, 85, "1"),   # inside: high<ph, low>pl
        (100, 90, 100, 90, "1"),   # inside: exactly equal (boundary)
        (110, 90, 105, 85, "2U"),  # new high, no new low
        (100, 80, 105, 85, "2D"),  # new low, no new high
        (110, 80, 105, 85, "3"),   # outside: new high AND new low
        (106, 84, 105, 85, "3"),   # outside (1 tick each side)
    ]
    passed = 0
    for h, l, ph, pl, expected in tests:
        result = classify_bar(h, l, ph, pl)
        status = "PASS" if result == expected else "FAIL"
        if status == "PASS":
            passed += 1
        print(f"  [{status}] classify_bar({h},{l},{ph},{pl}) = {result!r}  (expected {expected!r})")

    print(f"\n{passed}/{len(tests)} tests passed")
    assert passed == len(tests), "Some tests failed!"
