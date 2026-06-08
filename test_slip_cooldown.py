"""Unit tests for GUARDIAN-S slip-close cooldown gate (is_slip_cooldown_active).

Churn fix #2/#3 (2026-06-06 BTC): after a GUARDIAN-M slip-close, the entry path kept
re-firing — the pyramid tried every layer in the same scan (#2) and the next 5-min tick
re-entered (#3), each paying spread. One cooldown covers both: a slip-close stamps a
per-symbol time; any entry attempt within `cooldown_minutes` is blocked — the next layer
in the same scan AND subsequent ticks.

mt5 is not exercised: slip_close_at + now_ts are injected so the gate is tested in isolation.
"""
from risk_manager import RiskManager, SLIP_COOLDOWN_MINUTES

T = 1_780_000_000  # POSIX server-time anchor (seconds)


def test_no_prior_slip_close_allows_entry():
    rm = RiskManager()
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=None, now_ts=T) is False


def test_same_tick_after_slip_close_blocks():
    # 0s elapsed — the next pyramid layer in the same scan (#2)
    rm = RiskManager()
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=T, now_ts=T) is True


def test_within_cooldown_blocks():
    # 2 min after a slip-close, cooldown 5 → next tick still blocked (#3)
    rm = RiskManager()
    now = T + 2 * 60
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=T, now_ts=now,
                                      cooldown_minutes=5) is True


def test_after_cooldown_allows_entry():
    rm = RiskManager()
    now = T + 6 * 60   # 6 min later, cooldown 5 → expired
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=T, now_ts=now,
                                      cooldown_minutes=5) is False


def test_boundary_exactly_at_cooldown_allows():
    # elapsed == cooldown is NOT active (strict <), mirroring other gates
    rm = RiskManager()
    now = T + 5 * 60
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=T, now_ts=now,
                                      cooldown_minutes=5) is False


def test_default_cooldown_constant_used():
    # With the module default, a slip-close just under the window still blocks
    rm = RiskManager()
    now = T + SLIP_COOLDOWN_MINUTES * 60 - 1
    assert rm.is_slip_cooldown_active("BTCUSDm", slip_close_at=T, now_ts=now) is True
