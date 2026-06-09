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

def test_exactly_at_cooldown_boundary_allows():
    rows = [_row(SL, 60), _row(SL, 80), _row(SL, 95)]  # newest exactly 60m ago
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False


# ── Integration tests (real SQLite DB) ────────────────────────────────────────

import sqlite3, pathlib


def _seed_db(path, trades):
    """trades: list of (symbol, exit_time_or_None, exit_reason, net_profit)."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE trade_history (
        ticket INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, order_type TEXT,
        lot_size REAL, entry_time TEXT, entry_price REAL, entry_reason TEXT,
        slippage REAL, exit_time TEXT, exit_price REAL, net_profit REAL,
        max_floating_profit REAL, max_floating_loss REAL, exit_reason TEXT,
        balance_after_trade REAL)""")
    for sym, xt, xr, net in trades:
        conn.execute("INSERT INTO trade_history (symbol, exit_time, exit_reason, net_profit) "
                     "VALUES (?,?,?,?)", (sym, xt, xr, net))
    conn.commit(); conn.close()


def test_query_returns_top3_newest_and_trips(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", "2026-06-08 19:05:00", SL, -12.98),
        ("XAUUSDm", "2026-06-08 19:25:00", SL, -15.54),
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),   # newest SL
        ("BTCUSDm", "2026-06-08 19:36:00", SL, -5.0),     # other symbol, ignore
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)   # 5m after newest XAU SL
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is True


def test_query_ignores_open_positions(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", None,                  SL, 0.0),      # open (exit_time NULL) — must be ignored
        ("XAUUSDm", "2026-06-08 19:05:00", SL, -12.98),
        ("XAUUSDm", "2026-06-08 19:25:00", SL, -15.54),
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is True


def test_query_tp_in_top3_allows(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", "2026-06-08 18:00:00", SL, -12.0),
        ("XAUUSDm", "2026-06-08 19:25:00", TP, +13.0),    # a win in the top-3
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is False


def test_db_error_fails_open(tmp_path):
    rm = RiskManager(db_path=str(tmp_path / "does_not_exist_dir" / "nope.db"))
    # Bad path → sqlite error inside _recent_exits → fail-open False
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                    now=datetime(2026, 6, 8, 20, 0, 0)) is False
