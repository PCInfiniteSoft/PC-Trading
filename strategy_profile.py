# strategy_profile.py
"""Per-symbol strategy profile accessors (SP-A).

Pure functions over bot_config.SYMBOLS_CONFIG[symbol]["strategy"]. No MT5/IO.
Centralizes the per-symbol logic that used to be inline in trade_manager.py.
"""
from advanced_indicators import rsi_cross_down
from bot_config import SYMBOLS_CONFIG

_EMPTY = {
    "entry_paths": ["mean_reversion"],
    "guards": {},
    "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
    "xau_trend_sell_enabled": False,
}


def get_profile(symbol: str) -> dict:
    return SYMBOLS_CONFIG.get(symbol, {}).get("strategy", _EMPTY)


def has_path(symbol: str, path: str) -> bool:
    return path in get_profile(symbol).get("entry_paths", [])


def guard_enabled(symbol: str, guard: str) -> bool:
    return bool(get_profile(symbol).get("guards", {}).get(guard, False))


def score_blacklist(symbol: str) -> set:
    return get_profile(symbol).get("guards", {}).get("score_blacklist", set())


def trend_sell_enabled(symbol: str) -> bool:
    return bool(get_profile(symbol).get("xau_trend_sell_enabled", False))


def trend_sell_cfg(symbol: str) -> dict:
    return get_profile(symbol).get("trend_sell", _EMPTY["trend_sell"])


# ── Task 4: live trend-sell signal wrapper ───────────────────────────────────
def trend_sell_signal(symbol: str, m5_closes, d1_trend: str) -> bool:
    """st3 trend-sell trigger for live use: RSI cross-down through the profile's
    `rsi_level`, gated on a D1 DOWNTREND and the symbol declaring a trend_sell path.

    `m5_closes`: DataFrame with a 'close' column, most-recent bar last.
    Returns True only when all gates pass.
    """
    if not has_path(symbol, "trend_sell"):
        return False
    if d1_trend != "DOWNTREND":
        return False
    cfg = trend_sell_cfg(symbol)
    if cfg.get("trigger") != "rsi":
        return False
    return bool(rsi_cross_down(m5_closes, level=cfg.get("rsi_level", 50.0)))
