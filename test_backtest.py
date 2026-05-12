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
