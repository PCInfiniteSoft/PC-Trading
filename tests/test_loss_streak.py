"""Unit tests for GUARDIAN-R loss-streak circuit breaker (is_loss_streak_active)."""
from datetime import datetime, timedelta
from risk_manager import RiskManager

SL  = "Hit Stop Loss 🛡️"
SLR = "Hit Stop Loss 🛡️ [recovered]"
TP  = "Hit Take Profit 🎯"
SLIP = "GUARDIAN-M: slip 421pts"
NOW = datetime(2026, 6, 8, 20, 0, 0)

def _row(reason, minutes_ago):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return (t, reason)

def _rm():
    return RiskManager(db_path=":memory:")

def test_three_sl_within_cooldown_blocks():
    rows = [_row(SL, 5), _row(SL, 30), _row(SL, 50)]   # newest 5m ago
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is True

def test_three_sl_cooldown_expired_allows():
    rows = [_row(SL, 61), _row(SL, 80), _row(SL, 95)]  # newest 61m ago > 60m
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_tp_in_window_breaks_streak():
    rows = [_row(SL, 5), _row(TP, 20), _row(SL, 40)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_fewer_than_n_trades_allows():
    rows = [_row(SL, 5), _row(SL, 30)]                  # only 2
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_disabled_flag_allows():
    rows = [_row(SL, 5), _row(SL, 30), _row(SL, 50)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows, enabled=False) is False

def test_recovered_sl_counts_as_loss():
    rows = [_row(SLR, 5), _row(SLR, 30), _row(SLR, 50)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is True

def test_slip_close_does_not_count_and_breaks_streak():
    rows = [_row(SLIP, 5), _row(SL, 30), _row(SL, 50)]  # newest is a slip-close
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False
