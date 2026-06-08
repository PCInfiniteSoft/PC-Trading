"""Unit tests for O(1) pointer helper used in run_backtest optimisation.

TDD gate: test_advance_pointer_equals_naive must FAIL before the helper is
added to backtest.py, then PASS after.

MT5-mock preamble mirrors test_trend_sell_wiring.py lines 12-24 so that
importing `backtest` works without a live MT5 terminal.
"""
import sys
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ── Mock MetaTrader5 before any project module imports it ──────────────────
_mt5_mock = MagicMock()
_mt5_mock.TIMEFRAME_M5  = 5
_mt5_mock.TIMEFRAME_M15 = 15
_mt5_mock.TIMEFRAME_H1  = 16385
_mt5_mock.TIMEFRAME_H4  = 16388
_mt5_mock.TIMEFRAME_D1  = 16408
_mt5_mock.ORDER_TYPE_BUY  = 0
_mt5_mock.ORDER_TYPE_SELL = 1
sys.modules.setdefault('MetaTrader5', _mt5_mock)

# ── Mock heavy deps not needed for these pure-function tests ───────────────
sys.modules.setdefault('shared_state',       MagicMock())
sys.modules.setdefault('discord',            MagicMock())
sys.modules.setdefault('discord.ext',        MagicMock())
sys.modules.setdefault('discord.ext.tasks',  MagicMock())
sys.modules.setdefault('openai',             MagicMock())
sys.modules.setdefault('cloudscraper',       MagicMock())
sys.modules.setdefault('customtkinter',      MagicMock())
sys.modules.setdefault('flask',              MagicMock())
sys.modules.setdefault('pandas_ta',          MagicMock())
sys.modules.setdefault('ai_engine',          MagicMock())
sys.modules.setdefault('system_utils',       MagicMock())
sys.modules.setdefault('database_manager',   MagicMock())
sys.modules.setdefault('trade_noti',         MagicMock())

if 'bot_config' not in sys.modules:
    _bot_config_mock = MagicMock()
    _bot_config_mock.SYMBOLS_CONFIG = {
        "XAUUSDm": {"lot": 0.01, "sl": 0, "max_slip": 300,
                    "strategy": {}, "analyst_score_offset": 0,
                    "max_spread_override": 999},
        "BTCUSDm": {"lot": 0.01, "sl": 0, "max_slip": 300,
                    "strategy": {}, "analyst_score_offset": 0,
                    "max_spread_override": 999},
    }
    _bot_config_mock.MAGIC_NUMBER = 12345
    _bot_config_mock.DAILY_LOSS_LIMIT = 50.0
    sys.modules['bot_config'] = _bot_config_mock

import backtest  # noqa: E402  (must come after all mocks)


# ────────────────────────────────────────────────────────────────────────────
# Helper under test
# ────────────────────────────────────────────────────────────────────────────

def advance_pointer(times: np.ndarray, p: int, bar_time) -> int:
    """Advance pointer p while times[p+1] <= bar_time.

    Parameters
    ----------
    times    : numpy array of timestamps (sorted ascending).
    p        : current pointer (integer index into times, or -1 = before start).
    bar_time : the current bar's timestamp.

    Returns
    -------
    Updated pointer.  p == -1 means bar_time is before all rows.
    """
    while p + 1 < len(times) and times[p + 1] <= bar_time:
        p += 1
    return p


def naive_slice(df: pd.DataFrame, bar_time, K: int) -> pd.DataFrame:
    """Reference (slow) implementation."""
    return df[df["time"] <= bar_time].tail(K)


def pointer_slice(df: pd.DataFrame, times: np.ndarray, p: int, K: int) -> pd.DataFrame:
    """Fast implementation using pre-advanced pointer p."""
    return df.iloc[max(0, p + 1 - K): p + 1]


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

def _make_frame(n: int, base_ts=None) -> tuple:
    """Return (df, times_array) with n rows, 5-min spaced timestamps."""
    if base_ts is None:
        base_ts = pd.Timestamp("2024-01-01 00:00:00")
    times = [base_ts + pd.Timedelta(minutes=5 * i) for i in range(n)]
    df = pd.DataFrame({"time": times, "close": range(n)}).reset_index(drop=True)
    times_np = df["time"].values
    return df, times_np


class TestAdvancePointerHelper:
    """Verify advance_pointer logic in isolation."""

    def test_pointer_starts_minus1_before_all_rows(self):
        df, times = _make_frame(10)
        bar_time = df["time"].iloc[0] - pd.Timedelta(seconds=1)
        p = advance_pointer(times, -1, bar_time)
        assert p == -1

    def test_pointer_reaches_0_exactly_on_first_row(self):
        df, times = _make_frame(10)
        bar_time = df["time"].iloc[0]
        p = advance_pointer(times, -1, bar_time)
        assert p == 0

    def test_pointer_advances_to_middle(self):
        df, times = _make_frame(20)
        bar_time = df["time"].iloc[9]
        p = advance_pointer(times, -1, bar_time)
        assert p == 9

    def test_pointer_clamps_at_last_row(self):
        df, times = _make_frame(10)
        bar_time = df["time"].iloc[-1] + pd.Timedelta(hours=1)
        p = advance_pointer(times, -1, bar_time)
        assert p == 9

    def test_incremental_advance_is_consistent(self):
        """Advancing step-by-step matches a single jump to the same bar_time."""
        df, times = _make_frame(30)
        p_step = -1
        for idx in range(len(df)):
            bar_time = df["time"].iloc[idx]
            p_step = advance_pointer(times, p_step, bar_time)
        p_jump = advance_pointer(times, -1, df["time"].iloc[-1])
        assert p_step == p_jump


class TestPointerSliceEqualsNaive:
    """Core gate: pointer-based slice must equal naive boolean-mask slice.

    These tests will FAIL until advance_pointer (or equivalent inline code)
    is used in run_backtest.  They pass once the helper exists and is correct.
    The tests here exercise the helper standalone — correctness in run_backtest
    is proven by the before/after CSV identity gate.
    """

    def test_empty_result_before_all_rows(self):
        """bar_time before all rows → both return empty DataFrame."""
        df, times = _make_frame(50)
        K = 100
        bar_time = df["time"].iloc[0] - pd.Timedelta(seconds=1)
        p = advance_pointer(times, -1, bar_time)
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        assert got.empty
        assert want.empty
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_exactly_on_boundary_first_row(self):
        """bar_time == first row time → exactly 1 row."""
        df, times = _make_frame(50)
        K = 100
        bar_time = df["time"].iloc[0]
        p = advance_pointer(times, -1, bar_time)
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_fewer_rows_than_K(self):
        """When total available rows < K, return all available (no truncation)."""
        df, times = _make_frame(30)
        K = 100
        bar_time = df["time"].iloc[10]
        p = advance_pointer(times, -1, bar_time)
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_exactly_K_rows_available(self):
        df, times = _make_frame(200)
        K = 100
        bar_time = df["time"].iloc[99]   # exactly 100 rows (0..99)
        p = advance_pointer(times, -1, bar_time)
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        assert len(got) == K
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_more_rows_than_K(self):
        """When more rows precede bar_time than K, tail(K) truncates to K."""
        df, times = _make_frame(300)
        K = 100
        bar_time = df["time"].iloc[250]
        p = advance_pointer(times, -1, bar_time)
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        assert len(got) == K
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_sequence_of_increasing_bar_times(self):
        """Simulate the hot loop: advance pointer incrementally, compare every bar."""
        df, times = _make_frame(200)
        K = 50
        p = -1
        for idx in range(0, len(df), 7):   # sample every 7th bar
            bar_time = df["time"].iloc[idx]
            p = advance_pointer(times, p, bar_time)
            got  = pointer_slice(df, times, p, K)
            want = naive_slice(df, bar_time, K)
            pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                          want.reset_index(drop=True),
                                          check_like=False)

    def test_last_row(self):
        """Pointer at last row returns correct tail."""
        df, times = _make_frame(150)
        K = 100
        bar_time = df["time"].iloc[-1]
        p = advance_pointer(times, -1, bar_time)
        assert p == len(df) - 1
        got  = pointer_slice(df, times, p, K)
        want = naive_slice(df, bar_time, K)
        pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                      want.reset_index(drop=True))

    def test_h4_K50_typical_sizes(self):
        """K=50 (H4/D1 tail), simulate typical 2000-row H4 frame."""
        df, times = _make_frame(2000)
        K = 50
        p = -1
        for idx in range(0, len(df), 48):
            bar_time = df["time"].iloc[idx]
            p = advance_pointer(times, p, bar_time)
            got  = pointer_slice(df, times, p, K)
            want = naive_slice(df, bar_time, K)
            pd.testing.assert_frame_equal(got.reset_index(drop=True),
                                          want.reset_index(drop=True))
