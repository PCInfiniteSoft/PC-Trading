#!/usr/bin/env python3
"""PC Trading — Backtest (Approach A: bar-by-bar simulation)."""
import argparse
import sys
import MetaTrader5 as mt5
from bot_config import SYMBOLS_CONFIG, ACCOUNT_ID, PWD, SRV


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
