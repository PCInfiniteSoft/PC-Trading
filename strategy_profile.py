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


# ── Task 4: live trend-sell signal wrapper ───────────────────────────────────
# NOTE: backtest.py imports MetaTrader5 at module level, but the import works
# cleanly when MT5 is installed (as on the trading server). In test environments
# the test suite mocks MetaTrader5 at sys.modules before any backtest import.
# Keeping the import at module level is safe; it is inside this function to avoid
# pulling in MT5 when strategy_profile is imported by lightweight callers that
# do not need the backtest module.
def trend_sell_signal(symbol: str, m5_closes, d1_trend: str) -> bool:
    """st3 trigger for live use: RSI cross-down through `rsi_level`, gated on D1 DOWNTREND.

    `m5_closes` is a DataFrame with a 'close' column (most recent bar last), mirroring the
    backtest `m5_slice`. Returns False unless the symbol's profile lists the trend_sell path.

    DRY: reuses backtest.rsi_cross_down (the validated predicate) rather than re-implementing
    RSI logic here. No MT5 calls inside — caller passes data.
    """
    import backtest  # inside function: avoids MT5 import side-effects for lightweight callers

    if not has_path(symbol, "trend_sell"):
        return False
    if str(d1_trend) != "DOWNTREND":
        return False
    cfg = trend_sell_cfg(symbol)
    if cfg.get("trigger") != "rsi":
        return False
    return bool(backtest.rsi_cross_down(m5_closes, level=cfg.get("rsi_level", 50.0)))
