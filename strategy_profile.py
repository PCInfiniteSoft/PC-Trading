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
def trend_sell_signal(symbol: str, m5_closes, d1_trend: str) -> bool:
    """st3 trigger for live use: RSI cross-down through `rsi_level`, gated on D1 DOWNTREND.

    `m5_closes` is a DataFrame with a 'close' column (most recent bar last), mirroring the
    backtest `m5_slice`. Returns False unless the symbol's profile lists the trend_sell path.

    DRY: reuses backtest.rsi_cross_down (the validated predicate) rather than re-implementing
    RSI logic here. No MT5 calls inside — caller passes data.

    `import backtest` is deferred (inside the function body) for two reasons:
    1. Avoids circular-import risk: strategy_profile is a lightweight live module; pulling in
       the full backtest harness (which imports MT5, advanced_indicators, etc.) at module level
       would make every importer of strategy_profile transitively depend on MT5.
    2. Keeps the hot-loop caller cheap: trade_manager imports strategy_profile on every tick;
       a module-level backtest import would chain-load MT5 + pandas on startup even when
       trend_sell is disabled or the symbol has no trend_sell path.
    NOTE for Task 5: test_strategy_profile.py does NOT mock MetaTrader5. When the XAU
    trend_sell path is enabled and the xfail marker is removed, this import executes for real.
    It is safe on the trading server (MT5 installed), but consider copying the
    sys.modules mock from test_backtest.py if the suite needs to run without MT5.
    """
    import backtest  # deferred — see docstring

    if not has_path(symbol, "trend_sell"):
        return False
    if str(d1_trend) != "DOWNTREND":
        return False
    cfg = trend_sell_cfg(symbol)
    if cfg.get("trigger") != "rsi":
        return False
    return bool(backtest.rsi_cross_down(m5_closes, level=cfg.get("rsi_level", 50.0)))
