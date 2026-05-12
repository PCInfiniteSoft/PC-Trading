#!/usr/bin/env python3
"""PC Trading — Backtest (Approach A: bar-by-bar simulation)."""
import argparse
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

from bot_config import SYMBOLS_CONFIG, ACCOUNT_ID, PWD, SRV

TF_MAP = {
    "M5": mt5.TIMEFRAME_M5,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def load_data(symbol: str, months: int) -> dict:
    """Fetch OHLCV candles for all needed timeframes. Returns {tf_name: DataFrame}."""
    end   = datetime.now()
    start = end - timedelta(days=months * 31)
    result = {}
    for tf_name, tf_const in TF_MAP.items():
        rates = mt5.copy_rates_range(symbol, tf_const, start, end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No data for {symbol} {tf_name} — is MT5 connected and symbol active?")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        result[tf_name] = df[["time", "open", "high", "low", "close", "tick_volume"]].reset_index(drop=True)
    return result


def load_symbol_info(symbol: str) -> dict:
    """Return point size and tick value for a symbol."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol info not found for {symbol}")
    return {"point": info.point, "tick_value": info.trade_tick_value}


def parse_args():
    p = argparse.ArgumentParser(description="PC Trading backtest")
    p.add_argument("--months",  type=int, default=3, metavar="N",
                   help="Lookback period in months (default: 3)")
    p.add_argument("--symbols", nargs="+", default=list(SYMBOLS_CONFIG.keys()),
                   metavar="SYM", help="Symbols to test")
    p.add_argument("--risk",    type=int, default=3, choices=range(1, 6), metavar="1-5",
                   help="Risk level 1-5 (default: 3)")
    p.add_argument("--export",  type=str, default=None, metavar="PATH",
                   help="Export trade log to CSV")
    return p.parse_args()


def init_mt5() -> bool:
    if not mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV):
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}")
        return False
    return True


def main():
    args = parse_args()
    if not init_mt5():
        sys.exit(1)
    try:
        trades = run_backtest(args)
        print_report(trades, args)
        if args.export:
            export_csv(trades, args.export)
    finally:
        mt5.shutdown()


def run_backtest(args):
    raise NotImplementedError


def print_report(trades, args):
    raise NotImplementedError


def export_csv(trades, path):
    raise NotImplementedError


if __name__ == "__main__":
    main()
