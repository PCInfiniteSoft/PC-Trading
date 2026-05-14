#!/usr/bin/env python3
"""Run all 5 backtest scenarios in one process — single MT5 connection, cached data."""
import sys
import types
import backtest

SCENARIOS = ["baseline", "s1", "s2", "s3", "s3a", "s4", "s5"]

def make_args(scenario, cached_data=None):
    a = types.SimpleNamespace()
    a.months   = 3
    a.symbols  = list(backtest.SYMBOLS_CONFIG.keys())
    a.risk     = 3
    a.capital  = 300.0
    a.lot      = 0.03
    a.spread   = 0.20
    a.scenario = scenario
    a.export   = f"{scenario}.csv"
    a._cached_data = cached_data  # pre-loaded {symbol: {tf: df}}
    return a

if not backtest.init_mt5():
    sys.exit(1)

try:
    # Load all data once upfront
    print("[CACHE] Loading market data for all symbols...", flush=True)
    cached = {}
    for sym in backtest.SYMBOLS_CONFIG.keys():
        print(f"[CACHE]   {sym}...", flush=True)
        cached[sym] = backtest.load_data(sym, months=3)
        info = backtest.load_symbol_info(sym)
        cached[sym]["_info"] = info
    print("[CACHE] Data loaded. Starting scenarios.\n", flush=True)

    for sc in SCENARIOS:
        print(f"\n{'='*60}")
        print(f"  RUNNING SCENARIO: {sc.upper()}")
        print('='*60, flush=True)
        args   = make_args(sc, cached_data=cached)
        trades = backtest.run_backtest(args)
        backtest.print_report(trades, args)
        backtest.export_csv(trades, args.export)
finally:
    import MetaTrader5 as mt5
    mt5.shutdown()

print("\nALL SCENARIOS COMPLETE", flush=True)
