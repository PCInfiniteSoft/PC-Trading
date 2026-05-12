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


def test_compute_scout_buy_with_aligned_ema_returns_positive():
    """_compute_scout returns > 0 when EMA20 > EMA50 for BUY direction."""
    import backtest
    h1 = make_ohlcv(100, trend="up", base=100.0)
    score = backtest._compute_scout(h1, "BUY")
    assert score >= 1


def test_compute_scout_insufficient_data_returns_zero():
    """_compute_scout returns 0 when slice has fewer than 60 rows."""
    import backtest
    h1 = make_ohlcv(30, trend="up")
    assert backtest._compute_scout(h1, "BUY") == 0


def test_compute_analyst_score_returns_dict_with_required_keys():
    """compute_analyst_score always returns dict with score and rsi keys."""
    import backtest
    m5 = make_ohlcv(100, trend="up")
    h1 = make_ohlcv(100, trend="up")
    result = backtest.compute_analyst_score(m5, h1, "BUY", rsi_threshold=45.0)
    assert "score" in result
    assert "rsi"   in result
    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 12


def test_compute_analyst_score_buy_on_uptrend_scores_supertrend():
    """compute_analyst_score gives +3 for Supertrend when BUY on uptrend data."""
    import backtest
    m5 = make_ohlcv(100, trend="up", base=100.0)
    h1 = make_ohlcv(100, trend="up", base=100.0)
    result = backtest.compute_analyst_score(m5, h1, "BUY", rsi_threshold=99.0)
    assert result["score"] >= 3


def _make_guardian_call(symbol="BTCUSDm", direction="BUY", allowed="BOTH",
                        last_sl_bar=None, current_bar=100, open_positions=None,
                        fixed_spread=100, max_spread=8000):
    import backtest
    return backtest.check_guardian(
        symbol=symbol, direction=direction,
        allowed_direction=allowed,
        last_sl_bar=last_sl_bar or {},
        current_bar_idx=current_bar,
        open_positions=open_positions or [],
        fixed_spread=fixed_spread,
        max_spread=max_spread,
    )


def test_guardian_passes_clean_state():
    allowed, reason = _make_guardian_call()
    assert allowed is True
    assert reason == "OK"


def test_guardian_blocks_cooldown():
    """Gate 1: blocks entry 1 bar after SL hit."""
    allowed, reason = _make_guardian_call(last_sl_bar={"BTCUSDm": 99}, current_bar=100)
    assert allowed is False
    assert reason == "COOLDOWN"


def test_guardian_allows_after_cooldown():
    """Gate 1: allows entry 2+ bars after SL hit."""
    allowed, reason = _make_guardian_call(last_sl_bar={"BTCUSDm": 98}, current_bar=100)
    assert allowed is True


def test_guardian_blocks_wrong_direction():
    """Gate 2: blocks BUY when DIRECTOR says SELL_ONLY."""
    allowed, reason = _make_guardian_call(direction="BUY", allowed="SELL_ONLY")
    assert allowed is False
    assert reason == "DIRECTION"


def test_guardian_blocks_excessive_spread():
    """Gate 3: blocks when fixed_spread > max_spread."""
    allowed, reason = _make_guardian_call(fixed_spread=10000, max_spread=8000)
    assert allowed is False
    assert reason == "SPREAD"


def test_guardian_blocks_max_layers():
    """Gate 4: blocks when symbol already has 3 open positions."""
    positions = [{"symbol": "BTCUSDm"} for _ in range(3)]
    allowed, reason = _make_guardian_call(open_positions=positions)
    assert allowed is False
    assert reason == "MAX_LAYERS"
