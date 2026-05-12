"""Unit tests for backtest.py pure functions. MT5 is mocked at import time."""
import sys
from unittest.mock import MagicMock
import pandas as pd
import numpy as np
import pytest

# Mock MT5 before backtest imports it
_mt5_mock = MagicMock()
_mt5_mock.TIMEFRAME_M5  = 5
_mt5_mock.TIMEFRAME_H1  = 16385
_mt5_mock.TIMEFRAME_H4  = 16388
_mt5_mock.TIMEFRAME_D1  = 16408
sys.modules['MetaTrader5'] = _mt5_mock

# Also mock heavy deps not needed for pure-function tests
sys.modules['shared_state']       = MagicMock()
sys.modules['discord']            = MagicMock()
sys.modules['discord.ext']        = MagicMock()
sys.modules['discord.ext.tasks']  = MagicMock()
sys.modules['openai']             = MagicMock()
sys.modules['cloudscraper']       = MagicMock()
sys.modules['customtkinter']      = MagicMock()
sys.modules['flask']              = MagicMock()


def make_ohlcv(n: int = 60, trend: str = "up", base: float = 100.0) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame for testing."""
    rows = []
    for i in range(n):
        if trend == "up":
            close = base + i * 0.5
        elif trend == "down":
            close = base - i * 0.5
        else:
            close = base + (i % 5 - 2) * 0.1
        rows.append({
            "time":        pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=5 * i),
            "open":        close - 0.1,
            "high":        close + 0.3,
            "low":         close - 0.3,
            "close":       close,
            "tick_volume": 100,
        })
    return pd.DataFrame(rows)


def test_parse_args_defaults():
    """parse_args returns correct defaults when called with empty argv."""
    import backtest
    old_argv = sys.argv
    sys.argv = ["backtest.py"]
    args = backtest.parse_args()
    sys.argv = old_argv
    assert args.months == 3
    assert args.risk == 3
    assert args.export is None


def test_load_data_returns_four_timeframes():
    """load_data returns dict with M5/H1/H4/D1 keys, each a non-empty DataFrame."""
    import backtest

    _mt5_mock.copy_rates_range.return_value = [
        {"time": i, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "tick_volume": 100}
        for i in range(200)
    ]

    data = backtest.load_data("BTCUSDm", months=1)

    assert set(data.keys()) == {"M5", "H1", "H4", "D1"}
    for tf in data:
        assert isinstance(data[tf], pd.DataFrame)
        assert len(data[tf]) > 0
        assert "close" in data[tf].columns
        assert "time" in data[tf].columns


def test_load_symbol_info_returns_point_and_tick_value():
    """load_symbol_info returns dict with 'point' and 'tick_value' keys."""
    import backtest

    mock_info = MagicMock()
    mock_info.point = 0.01
    mock_info.trade_tick_value = 1.0
    _mt5_mock.symbol_info.return_value = mock_info

    info = backtest.load_symbol_info("BTCUSDm")

    assert info["point"] == 0.01
    assert info["tick_value"] == 1.0


def test_get_trend_uptrend():
    """_get_trend returns UPTREND when close consistently above long_stop."""
    import backtest
    df = make_ohlcv(50, trend="up", base=100.0)
    assert backtest._get_trend(df) == "UPTREND"


def test_get_trend_downtrend():
    """_get_trend returns DOWNTREND when close consistently below short_stop."""
    import backtest
    df = make_ohlcv(50, trend="down", base=200.0)
    assert backtest._get_trend(df) == "DOWNTREND"


def test_compute_director_both_up_gives_buy_only():
    """compute_director returns BUY_ONLY when H4 and D1 both UPTREND."""
    import backtest
    h4 = make_ohlcv(50, trend="up")
    d1 = make_ohlcv(50, trend="up")
    result = backtest.compute_director(h4, d1)
    assert result["allowed_direction"] == "BUY_ONLY"
    assert result["h4_trend"] == "UPTREND"
    assert result["d1_trend"] == "UPTREND"


def test_compute_director_both_down_gives_sell_only():
    """compute_director returns SELL_ONLY when H4 and D1 both DOWNTREND."""
    import backtest
    h4 = make_ohlcv(50, trend="down", base=200.0)
    d1 = make_ohlcv(50, trend="down", base=200.0)
    result = backtest.compute_director(h4, d1)
    assert result["allowed_direction"] == "SELL_ONLY"


def test_compute_director_mixed_gives_both():
    """compute_director returns BOTH when H4/D1 trends differ."""
    import backtest
    h4 = make_ohlcv(50, trend="up")
    d1 = make_ohlcv(50, trend="down", base=200.0)
    result = backtest.compute_director(h4, d1)
    assert result["allowed_direction"] == "BOTH"
