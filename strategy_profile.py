# strategy_profile.py
"""Per-symbol strategy profile accessors (SP-A).

Pure functions over bot_config.SYMBOLS_CONFIG[symbol]["strategy"]. No MT5/IO.
Centralizes the per-symbol logic that used to be inline in trade_manager.py.
"""
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
