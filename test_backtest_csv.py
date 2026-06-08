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


def _write_csv(path, times):
    pd.DataFrame({
        "time": [int(pd.Timestamp(t).timestamp()) for t in times],
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "tick_volume": 10, "spread": 0, "real_volume": 0,
        "datetime": times,
    }).to_csv(path, index=False)


def test_load_data_csv_returns_all_tfs(tmp_path):
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        _write_csv(tmp_path / f"BTCUSDm_{tf}.csv", ["2020-01-01 00:00", "2020-01-01 00:05"])
    data = backtest.load_data_csv("BTCUSDm", str(tmp_path))
    assert set(data.keys()) == {"M5", "M15", "H1", "H4", "D1"}
    assert list(data["M5"].columns) == ["time", "open", "high", "low", "close", "tick_volume"]
    assert str(data["M5"]["time"].dtype).startswith("datetime64")
    assert len(data["M5"]) == 2


def test_load_data_csv_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError, match="CSV not found"):
        backtest.load_data_csv("BTCUSDm", str(tmp_path))


def test_load_data_csv_applies_trailing_months(tmp_path):
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        _write_csv(tmp_path / f"BTCUSDm_{tf}.csv",
                   ["2025-01-01 00:00", "2026-05-01 00:00", "2026-06-01 00:00"])
    data = backtest.load_data_csv("BTCUSDm", str(tmp_path), months=2)
    assert len(data["M5"]) == 2  # only the two 2026 bars survive
