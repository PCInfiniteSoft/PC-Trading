"""One-off: pull full available history (all trading TFs) for BTC+XAU to data/history/.
For dynamic-lot / conviction-table calibration. Re-runnable (overwrites)."""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

SYMBOLS = ["BTCUSDm", "XAUUSDm"]
TFS = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
       "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}
START = datetime(2010, 1, 1)
END = datetime(2026, 6, 8)
OUT = "data/history"

if not mt5.initialize():
    raise SystemExit(f"MT5 init FAIL {mt5.last_error()}")

for sym in SYMBOLS:
    if mt5.symbol_info(sym) is None:
        print(f"[SKIP] {sym} no symbol info"); continue
    mt5.symbol_select(sym, True)
    for name, tf in TFS.items():
        r = mt5.copy_rates_range(sym, tf, START, END)
        if r is None or len(r) == 0:
            print(f"[WARN] {sym} {name}: NONE ({mt5.last_error()})"); continue
        df = pd.DataFrame(r)
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
        path = f"{OUT}/{sym}_{name}.csv"
        df.to_csv(path, index=False)
        print(f"[OK] {sym} {name}: {len(df):>9,} bars -> {path} "
              f"({df['datetime'].iloc[0]:%Y-%m-%d} .. {df['datetime'].iloc[-1]:%Y-%m-%d})")

mt5.shutdown()
print("DONE")
