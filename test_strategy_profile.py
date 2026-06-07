# test_strategy_profile.py
from bot_config import SYMBOLS_CONFIG


def test_btc_profile_mirrors_current_behavior():
    p = SYMBOLS_CONFIG["BTCUSDm"]["strategy"]
    assert p["entry_paths"] == ["mean_reversion"]          # BTC: no trend-sell yet
    assert p["guards"]["xau_buy_only"] is False
    assert p["guards"]["score_blacklist"] == {8}
    assert p["xau_trend_sell_enabled"] is False


def test_xau_profile_mirrors_current_behavior():
    p = SYMBOLS_CONFIG["XAUUSDm"]["strategy"]
    # Behavior-preserving: XAU still buy-only until A2 toggle flips on.
    assert p["entry_paths"] == ["mean_reversion"]
    assert p["guards"]["xau_buy_only"] is True
    assert p["guards"]["score_blacklist"] == {8}
    assert p["xau_trend_sell_enabled"] is False
    assert p["trend_sell"] == {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5}
