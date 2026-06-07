"""Unit tests for Task 6 wiring: get_recent_m5_closes and scaled_lot.

MT5 and all heavy dependencies are mocked before any project module is imported.
Pattern mirrors test_backtest.py lines 8-25.
"""
import sys
import types
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

# ── Mock MetaTrader5 before any project module imports it ──────────────────────
_mt5_mock = MagicMock()
_mt5_mock.TIMEFRAME_M5  = 5
_mt5_mock.TIMEFRAME_M15 = 15
_mt5_mock.TIMEFRAME_H1  = 16385
_mt5_mock.TIMEFRAME_H4  = 16388
_mt5_mock.TIMEFRAME_D1  = 16408
_mt5_mock.ORDER_TYPE_BUY  = 0
_mt5_mock.ORDER_TYPE_SELL = 1
sys.modules['MetaTrader5'] = _mt5_mock

# ── Mock heavy deps not needed for these pure-function tests ───────────────────
sys.modules['shared_state']       = MagicMock()
sys.modules['discord']            = MagicMock()
sys.modules['discord.ext']        = MagicMock()
sys.modules['discord.ext.tasks']  = MagicMock()
sys.modules['openai']             = MagicMock()
sys.modules['cloudscraper']       = MagicMock()
sys.modules['customtkinter']      = MagicMock()
sys.modules['flask']              = MagicMock()
sys.modules['pandas_ta']          = MagicMock()
sys.modules['ai_engine']          = MagicMock()
sys.modules['system_utils']       = MagicMock()
sys.modules['database_manager']   = MagicMock()
sys.modules['trade_noti']         = MagicMock()
sys.modules['risk_manager']       = MagicMock()

# bot_config needs SYMBOLS_CONFIG and MAGIC_NUMBER at minimum
_bot_config_mock = MagicMock()
_bot_config_mock.SYMBOLS_CONFIG = {
    "XAUUSD": {"lot": 0.01, "sl": 0, "max_slip": 300,
                "strategy": {
                    "entry_paths": ["mean_reversion", "trend_sell"],
                    "guards": {},
                    "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
                    "xau_trend_sell_enabled": False,
                }},
}
_bot_config_mock.MAGIC_NUMBER = 12345
_bot_config_mock.DAILY_LOSS_LIMIT = 50.0
# Make wildcard 'from bot_config import *' resolve SYMBOLS_CONFIG, MAGIC_NUMBER, etc.
_bot_config_mock.__all__ = ["SYMBOLS_CONFIG", "MAGIC_NUMBER", "DAILY_LOSS_LIMIT"]
sys.modules['bot_config'] = _bot_config_mock

import advanced_indicators as adv  # noqa: E402
from trade_manager import scaled_lot  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# Part A — get_recent_m5_closes
#
# NOTE: test_strategy_profile.py (collected before this file) imports
# strategy_profile → advanced_indicators WITHOUT mocking MetaTrader5 first,
# so adv.mt5 is the REAL mt5 module (a C extension).  We cannot set
# .return_value on a C builtin.  Instead, use unittest.mock.patch to replace
# adv.mt5.copy_rates_from_pos in each test, which works regardless of whether
# adv.mt5 is a MagicMock or the real module.
# ═════════════════════════════════════════════════════════════════════════════

def test_get_recent_m5_closes_none_returns_empty():
    """When MT5 returns None, result is an empty DataFrame with 'close' column."""
    with patch.object(adv.mt5, "copy_rates_from_pos", return_value=None):
        result = adv.get_recent_m5_closes("XAUUSD", bars=120)
    assert isinstance(result, pd.DataFrame), "must return a DataFrame"
    assert "close" in result.columns, "must have 'close' column"
    assert len(result) == 0, "must be empty when MT5 returns None"


def test_get_recent_m5_closes_empty_array_returns_empty():
    """When MT5 returns an empty list, result is an empty DataFrame."""
    with patch.object(adv.mt5, "copy_rates_from_pos", return_value=[]):
        result = adv.get_recent_m5_closes("XAUUSD", bars=120)
    assert "close" in result.columns
    assert len(result) == 0


def test_get_recent_m5_closes_happy_path():
    """When MT5 returns N bars, result has N rows with correct close values."""
    fake_rates = [{"close": 1900.0 + i * 0.5} for i in range(10)]
    with patch.object(adv.mt5, "copy_rates_from_pos", return_value=fake_rates):
        result = adv.get_recent_m5_closes("XAUUSD", bars=10)
    assert isinstance(result, pd.DataFrame)
    assert "close" in result.columns
    assert len(result) == 10
    assert list(result["close"]) == [r["close"] for r in fake_rates]


# ═════════════════════════════════════════════════════════════════════════════
# Part B — scaled_lot pure function
# ═════════════════════════════════════════════════════════════════════════════

def test_scaled_lot_mult_1_identity():
    """mult=1.0 must return base unchanged (production default path)."""
    assert scaled_lot(0.10, 1.0, 0.01, 0.01) == 0.10
    assert scaled_lot(0.01, 1.0, 0.01, 0.01) == 0.01


def test_scaled_lot_half_standard():
    """base=0.10, mult=0.5 -> 0.05 (0.10 * 0.5 = 0.05, step=0.01, min=0.01)."""
    result = scaled_lot(0.10, 0.5, 0.01, 0.01)
    assert result == pytest.approx(0.05), f"expected 0.05, got {result}"


def test_scaled_lot_clamped_to_min():
    """base=0.01, mult=0.5 -> floored at volume_min=0.01."""
    # 0.01 * 0.5 = 0.005 -> round to step 0.01 -> 0.01 = 0.00 (rounds down to 0),
    # then max(0.01, 0.00) = 0.01
    result = scaled_lot(0.01, 0.5, 0.01, 0.01)
    assert result == pytest.approx(0.01), f"expected 0.01 (clamped), got {result}"


def test_scaled_lot_larger_base():
    """base=1.0, mult=0.5, step=0.01, min=0.01 -> 0.50."""
    result = scaled_lot(1.0, 0.5, 0.01, 0.01)
    assert result == pytest.approx(0.50), f"expected 0.50, got {result}"


def test_scaled_lot_zero_step_uses_default():
    """volume_step=0 is defended against; falls back to 0.01."""
    result = scaled_lot(0.10, 0.5, 0.01, 0.0)
    assert result == pytest.approx(0.05)


def test_scaled_lot_none_step_uses_default():
    """volume_step=None is defended against; falls back to 0.01."""
    result = scaled_lot(0.10, 0.5, 0.01, None)
    assert result == pytest.approx(0.05)
