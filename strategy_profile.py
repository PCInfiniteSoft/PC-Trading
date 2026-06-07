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
    """st3 trigger for live use: RSI cross-down through `rsi_level`, gated on D1 DOWNTREND.

    `m5_closes` is a DataFrame with a 'close' column (most recent bar last), mirroring the
    backtest `m5_slice`. Returns False unless the symbol's profile lists the trend_sell path.

    DRY: reuses rsi_cross_down from advanced_indicators (the shared, validated predicate)
    rather than re-implementing RSI logic here. No MT5 calls inside — caller passes data.

    advanced_indicators is a lightweight shared module already loaded in the live process
    (imported by trade_manager → advanced_indicators at startup). The module-level import
    at the top of this file is therefore free.
    """
    if not has_path(symbol, "trend_sell"):
        return False
    if str(d1_trend) != "DOWNTREND":
        return False
    cfg = trend_sell_cfg(symbol)
    if cfg.get("trigger") != "rsi":
        return False
    return bool(rsi_cross_down(m5_closes, level=cfg.get("rsi_level", 50.0)))
