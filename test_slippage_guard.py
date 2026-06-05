"""
Unit tests for GUARDIAN-M dynamic slippage threshold (compute_dyn_slip).
No MT5 terminal: price/point/atr/spread/cfg are injected directly.
"""
from risk_manager import compute_dyn_slip

# Provisional per-symbol config (mirrors bot_config defaults)
BTC = {"slip_base": 600, "slip_a_atr": 0.02, "slip_b_spread": 1.0, "slip_cap": 1800}
XAU = {"slip_base": 300, "slip_a_atr": 0.02, "slip_b_spread": 1.0, "slip_cap": 900}


def test_base_only_when_atr_and_spread_missing():
    # atr_pct None and ask==bid (zero spread) -> only base contributes.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=100000.0, bid=100000.0, cfg=BTC)
    assert dyn == 600
    assert bd["atr"] == 0.0 and bd["spread"] == 0.0


def test_high_atr_widens_threshold():
    # BTC atr_pct=0.3% -> atr_pts = 0.003*100000/0.01 = 30000; term = 0.02*30000 = 600.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=0.3,
                               ask=100000.0, bid=100000.0, cfg=BTC)
    assert bd["atr"] == 600.0
    assert dyn == 1200  # 600 base + 600 atr, under cap 1800


def test_wide_spread_widens_threshold():
    # spread = (ask-bid)/point = (100010-100000)/0.01 = 1000 pts; term = 1.0*1000 = 1000.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=100010.0, bid=100000.0, cfg=BTC)
    assert bd["spread"] == 1000.0
    assert dyn == 1600  # 600 + 1000, under cap


def test_clamped_at_cap():
    # Huge ATR -> raw far over cap -> clamped to slip_cap.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=5.0,
                               ask=100010.0, bid=100000.0, cfg=BTC)
    assert dyn == 1800
    assert bd["raw"] > 1800


def test_never_tighter_than_base_non_regression():
    # Any non-negative terms -> dyn >= base (the non-regression invariant).
    dyn, _ = compute_dyn_slip(price=3300.0, point=0.001, atr_pct=0.0,
                              ask=3300.0, bid=3300.0, cfg=XAU)
    assert dyn >= 300


def test_xau_config_selected():
    # XAU base 300 distinct from BTC base 600.
    dyn, bd = compute_dyn_slip(price=3300.0, point=0.001, atr_pct=None,
                               ask=3300.0, bid=3300.0, cfg=XAU)
    assert bd["base"] == 300 and dyn == 300


def test_negative_or_zero_spread_ignored():
    # bid > ask (crossed/garbage) must not produce a negative spread term.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=99990.0, bid=100000.0, cfg=BTC)
    assert bd["spread"] == 0.0
    assert dyn == 600
