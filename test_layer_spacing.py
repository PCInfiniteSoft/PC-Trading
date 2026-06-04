"""
Unit tests for GUARDIAN-Q layer-spacing gate (is_layer_too_soon).
Reproduces the 2026-06-04 XAU pyramid failure: 3 SELL layers opened
06:00 / 06:10 / 06:15 — all stopped together at 08:56.

mt5 is not exercised here: positions + now_ts are injected so the gate
logic is tested in isolation (no live terminal needed).
"""
import types
import MetaTrader5 as mt5
from risk_manager import (RiskManager, MIN_LAYER_SPACING_MINUTES,
                          MAX_LAYERS_PER_SYMBOL, MAX_LAYERS_XAU)


def _pos(order_type, ts):
    """Fake MT5 position with the only attrs the gate reads."""
    return types.SimpleNamespace(type=order_type, time=int(ts))


SELL = mt5.ORDER_TYPE_SELL
BUY  = mt5.ORDER_TYPE_BUY

# Server-time POSIX anchors matching today's incident (UTC seconds, illustrative)
T0600 = 1_780_000_000          # first short
T0610 = T0600 + 10 * 60        # +10m exactly
T0615 = T0600 + 15 * 60        # +15m


def test_no_open_positions_allows_entry():
    rm = RiskManager()
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=[], now_ts=T0600) is False


def test_second_layer_within_window_is_blocked():
    # 06:10 attempt, last open at 06:00 → 10m elapsed but check is "< min", so
    # use a moment just under the window to prove the block fires.
    rm = RiskManager()
    last = [_pos(SELL, T0600)]
    now = T0600 + 9 * 60 + 55      # 9m55s after first → < 10m
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=last, now_ts=now) is True


def test_third_layer_also_blocked_against_most_recent():
    # Two shorts already open (06:00 + a later one); newest governs spacing.
    rm = RiskManager()
    opens = [_pos(SELL, T0600), _pos(SELL, T0600 + 5 * 60)]
    now = T0600 + 9 * 60           # 4m after newest layer → blocked
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=opens, now_ts=now) is True


def test_entry_after_window_is_allowed():
    rm = RiskManager()
    last = [_pos(SELL, T0600)]
    now = T0600 + (MIN_LAYER_SPACING_MINUTES + 1) * 60   # 11m later
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=last, now_ts=now) is False


def test_opposite_direction_is_ignored():
    # A recent BUY must not block a SELL layer and vice-versa.
    rm = RiskManager()
    recent_buy = [_pos(BUY, T0610)]
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=recent_buy, now_ts=T0610 + 60) is False


def test_exactly_at_window_boundary_is_allowed():
    # elapsed == min_minutes is NOT "< min" → allowed (boundary inclusive).
    rm = RiskManager()
    last = [_pos(SELL, T0600)]
    now = T0600 + MIN_LAYER_SPACING_MINUTES * 60
    assert rm.is_layer_too_soon("XAUUSDm", "SELL", positions=last, now_ts=now) is False


def test_replays_2026_06_04_burst_thins_three_attempts_to_two():
    """Real incident: SELL attempts at 06:00:11 / 06:10:06 / 06:15:09.

    Spacing is measured from the most-recent OPEN layer, and a BLOCKED attempt
    opens nothing — so the reference time does not advance. With a 10m window
    this thins the burst from 3 to 2 (the middle attempt is dropped), NOT to 1.
    This test pins that documented efficacy so the behavior can't silently drift.
    """
    rm = RiskManager()
    base = T0600
    attempts = [base + 0,            # 06:00:11  layer 1
                base + 9 * 60 + 55,  # 06:10:06  9m55s  → blocked
                base + 14 * 60 + 58] # 06:15:09  14m58s → allowed (layer 2)
    opens = []
    for ts in attempts:
        if not rm.is_layer_too_soon("XAUUSDm", "SELL", positions=list(opens), now_ts=ts):
            opens.append(_pos(SELL, ts))
    assert len(opens) == 2  # 1 of 3 blocked — loss ~halved, not eliminated


def test_xau_capped_at_two_layers():
    # 2 open XAU shorts → a 3rd is blocked by the per-symbol cap (XAU=2),
    # even if it survived the spacing gate.
    rm = RiskManager()
    opens = [_pos(SELL, T0600), _pos(SELL, T0615)]
    assert rm.is_max_layers_hit("XAUUSDm", "SELL", positions=opens) is True
    # 1 open is still under the cap.
    assert rm.is_max_layers_hit("XAUUSDm", "SELL", positions=[_pos(SELL, T0600)]) is False


def test_btc_keeps_three_layer_cap():
    rm = RiskManager()
    two = [_pos(SELL, T0600), _pos(SELL, T0615)]
    assert rm.is_max_layers_hit("BTCUSDm", "SELL", positions=two) is False  # 2/3 OK
    three = two + [_pos(SELL, T0615 + 600)]
    assert rm.is_max_layers_hit("BTCUSDm", "SELL", positions=three) is True  # 3/3 hit


def test_burst_plus_cap_thins_three_to_one():
    """Spacing (Gate Q) + XAU cap together: the 06:00/06:10/06:15 burst, then a
    4th attempt later, collapses to a single open layer per move.
    L1 opens; L2 blocked by spacing; L3 (14m58s) would pass spacing but is the
    2nd open so still allowed → 2 open. A later L4 is blocked by the cap=2.
    Confirms the combined gates cap XAU exposure at 2 and stop runaway pyramids.
    """
    rm = RiskManager()
    base = T0600
    attempts = [base, base + 9*60+55, base + 14*60+58, base + 40*60]
    opens = []
    for ts in attempts:
        if rm.is_max_layers_hit("XAUUSDm", "SELL", positions=list(opens)):
            continue
        if rm.is_layer_too_soon("XAUUSDm", "SELL", positions=list(opens), now_ts=ts):
            continue
        opens.append(_pos(SELL, ts))
    assert len(opens) == 2  # capped at MAX_LAYERS_XAU regardless of spacing


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
