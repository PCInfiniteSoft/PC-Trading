"""Unit tests for GUARDIAN-M slippage decision (churn fix #1, 2026-06-08).

Root cause (2026-06-06 BTC churn): place_order measured slippage as
`abs(fill - caller_price)/point`, where `caller_price` was a tick captured BEFORE a
slow `await ai.ai_analysis`. During volatile bursts the market drifted $20+ in that
window, so the stale reference inflated "slippage" to 2000+pts and GUARDIAN-M
false-closed every entry (−0.30 spread each), churning.

Fix: slippage must be measured against the live market price the order was actually
SENT at (a tick fetched immediately before order_send), not a stale caller snapshot.
`guardian_m_should_close` takes `send_ref_price` to make that explicit.

MT5/heavy deps mocked before importing trade_manager (mirrors test_trend_sell_wiring.py).
"""
import sys
from unittest.mock import MagicMock
import pytest

_mt5_mock = MagicMock()
_mt5_mock.ORDER_TYPE_BUY = 0
_mt5_mock.ORDER_TYPE_SELL = 1
sys.modules.setdefault('MetaTrader5', _mt5_mock)
sys.modules.setdefault('shared_state', MagicMock())
sys.modules.setdefault('discord', MagicMock())
sys.modules.setdefault('discord.ext', MagicMock())
sys.modules.setdefault('discord.ext.tasks', MagicMock())
sys.modules.setdefault('openai', MagicMock())
sys.modules.setdefault('cloudscraper', MagicMock())
sys.modules.setdefault('customtkinter', MagicMock())
sys.modules.setdefault('flask', MagicMock())
sys.modules.setdefault('pandas_ta', MagicMock())
sys.modules.setdefault('ai_engine', MagicMock())
sys.modules.setdefault('system_utils', MagicMock())
sys.modules.setdefault('database_manager', MagicMock())
sys.modules.setdefault('trade_noti', MagicMock())
# NOTE: do NOT mock risk_manager — test_layer_spacing.py (collected after this file)
# needs the REAL RiskManager, and a setdefault mock here would poison it. trade_manager
# imports the real risk_manager fine under the mocked MetaTrader5 above.

from trade_manager import guardian_m_should_close  # noqa: E402


def test_no_close_when_fill_matches_fresh_market():
    """Post-fix: reference is the FRESH send price. If the fill lands at the live
    market, slippage is ~0 and GUARDIAN-M does NOT fire — even though in the bug the
    caller's stale price was ~24pts ($0.24*?) away. The stale price never enters here."""
    should_close, slippage = guardian_m_should_close(
        fill_price=61543.0, send_ref_price=61543.0, point=0.01, max_slip=600)
    assert slippage == pytest.approx(0.0)
    assert should_close is False


def test_closes_on_genuine_slip():
    """A real bad fill (far from the price we sent at) still trips GUARDIAN-M."""
    should_close, slippage = guardian_m_should_close(
        fill_price=61600.0, send_ref_price=61543.0, point=0.01, max_slip=600)
    assert slippage == pytest.approx(5700.0)
    assert should_close is True


def test_boundary_not_closed_at_threshold():
    """slippage exactly == max_slip is allowed (strict >)."""
    should_close, slippage = guardian_m_should_close(
        fill_price=61549.0, send_ref_price=61543.0, point=0.01, max_slip=600)
    assert slippage == pytest.approx(600.0)
    assert should_close is False


def test_boundary_closed_just_over_threshold():
    should_close, slippage = guardian_m_should_close(
        fill_price=61549.01, send_ref_price=61543.0, point=0.01, max_slip=600)
    assert slippage == pytest.approx(601.0)
    assert should_close is True
