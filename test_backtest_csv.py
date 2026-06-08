"""Unit tests for the CSV data source (2026-06-08). mt5 mocked before importing backtest."""
import sys
from unittest.mock import MagicMock
import pandas as pd
import pytest

_mt5 = MagicMock()
_mt5.TIMEFRAME_M5, _mt5.TIMEFRAME_M15 = 5, 15
_mt5.TIMEFRAME_H1, _mt5.TIMEFRAME_H4, _mt5.TIMEFRAME_D1 = 16385, 16388, 16408
sys.modules.setdefault('MetaTrader5', _mt5)

import backtest  # noqa: E402


def _df(times):
    return pd.DataFrame({
        "time": pd.to_datetime(times),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1,
    })


def test_filter_zero_months_keeps_all():
    df = _df(["2018-01-01", "2020-01-01", "2026-01-01"])
    out = backtest._filter_trailing_months(df, 0)
    assert len(out) == 3


def test_filter_trailing_keeps_only_recent():
    # last bar 2026-06-01; 2 months back = ~2026-04-01 cutoff
    df = _df(["2026-01-01", "2026-05-01", "2026-06-01"])
    out = backtest._filter_trailing_months(df, 2)
    assert list(out["time"].dt.strftime("%Y-%m-%d")) == ["2026-05-01", "2026-06-01"]
