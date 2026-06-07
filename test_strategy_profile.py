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


# ── Task 2: strategy_profile.py accessor tests ──────────────────────────────
import strategy_profile as sp


def test_get_profile_returns_dict():
    assert sp.get_profile("XAUUSDm")["guards"]["xau_buy_only"] is True


def test_has_path():
    assert sp.has_path("BTCUSDm", "mean_reversion") is True
    assert sp.has_path("BTCUSDm", "trend_sell") is False


def test_guard_enabled_defaults_false_for_unknown_symbol():
    assert sp.guard_enabled("NOPE", "xau_buy_only") is False


def test_trend_sell_enabled():
    assert sp.trend_sell_enabled("XAUUSDm") is False


def test_score_blacklist():
    assert sp.score_blacklist("XAUUSDm") == {8}
    assert sp.score_blacklist("NOPE") == set()  # unknown symbol fallback


def test_trend_sell_cfg_fallback():
    cfg = sp.trend_sell_cfg("NOPE")
    assert cfg["trigger"] == "rsi"   # returns _EMPTY default
    assert cfg["rsi_level"] == 50.0
    assert cfg["lot_mult"] == 0.5


def test_btc_has_momentum_guards_xau_does_not():
    # GUARDIAN-N/O are BTC-only today; profile must preserve that exactly.
    assert sp.guard_enabled("BTCUSDm", "btc_momentum_guards") is True
    assert sp.guard_enabled("XAUUSDm", "btc_momentum_guards") is False
