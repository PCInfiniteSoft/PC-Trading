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


# ── Task 4: trend_sell_signal live wrapper tests ─────────────────────────────
import pandas as pd
import pytest


def _closes_crossing_down_through_50():
    # Zigzag series (slight upward bias) keeps RSI near 50-55, then a small drop
    # crosses it below 50. Verified: prev_rsi=53.81, now_rsi=48.89.
    # NOTE: spec's original data (ups=[100+i]*20 + downs=[120-3*i]*8) yields
    #   rsi_prev≈37, not ≥50, so backtest.rsi_cross_down returns False on it.
    #   Replaced with the verified-crossing zigzag from test_backtest.py:561-565.
    v, closes = 100.0, []
    for i in range(28):
        v += 0.5 if i % 2 == 0 else -0.3
        closes.append(v)
    closes.append(closes[-1] - 0.5)  # small drop to cross RSI below 50
    rows = [
        {"time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=5 * i),
         "open": c, "high": c + 0.3, "low": c - 0.3, "close": c, "tick_volume": 100}
        for i, c in enumerate(closes)
    ]
    return pd.DataFrame(rows)


@pytest.mark.xfail(reason="XAU trend_sell path enabled in Task 5", strict=True)
def test_trend_sell_fires_on_rsi_cross_in_downtrend():
    df = _closes_crossing_down_through_50()
    assert sp.trend_sell_signal("XAUUSDm", df, d1_trend="DOWNTREND") is True


def test_trend_sell_blocked_when_d1_not_downtrend():
    df = _closes_crossing_down_through_50()
    assert sp.trend_sell_signal("XAUUSDm", df, d1_trend="UPTREND") is False


def test_trend_sell_blocked_when_toggle_off_symbol_btc():
    df = _closes_crossing_down_through_50()
    # BTC has no trend_sell path; signal must be False regardless of price action.
    assert sp.trend_sell_signal("BTCUSDm", df, d1_trend="DOWNTREND") is False
