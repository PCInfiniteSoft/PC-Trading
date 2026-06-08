# Backtest CSV Data Source — Design

**Date:** 2026-06-08
**Status:** Design — approved, pending plan

## Problem

`backtest.py` reads candles only from a live MT5 terminal (`load_data` → `mt5.copy_rates_range`,
L36). Long-horizon runs are fragile: the 2026-06-07 `s3a_st3q` 7-year rerun died on an MT5
disconnect. The full ~7-year history was already fetched to `data/history/` (by `fetch_history.py`,
2026-06-07) but `backtest.py` cannot consume it. This blocks both (1) validating the live strategy
over 7 years and (2) the SP-C conviction-table replay, which needs the same long history.

## Goal

Let `backtest.py` run its existing scenarios over `data/history/` CSVs with **no live MT5
connection**, then run the 7-year validation (baseline vs st3). The simulation engine and scenarios
are unchanged — only the data source and symbol-info source become swappable.

## Scope

In scope: a CSV data source + baked symbol-info constants + CLI switch + running the 7-year
scenarios. **Out of scope:** SP-C bucket calibration / conviction table (that is plan-C); this only
provides the data-source enabler it depends on. No new metrics or analysis.

## Architecture

One swappable seam. `backtest.py` already funnels all candle access through `load_data(symbol,
months) -> {tf_name: DataFrame[time, open, high, low, close, tick_volume]}` and all symbol geometry
through `load_symbol_info(symbol) -> {point, tick_value}`. We add CSV-backed implementations behind a
`--data-source` switch; the bar-by-bar engine downstream is untouched.

### Components

1. **`load_data_csv(symbol, data_dir, start=None, end=None) -> dict`**
   Reads `{data_dir}/{symbol}_{TF}.csv` for each TF in `TF_MAP` (M5/M15/H1/H4/D1). The CSVs (written
   by `fetch_history.py`) have columns `time, open, high, low, close, tick_volume, spread,
   real_volume, datetime`. Return the SAME shape as `load_data`: `{tf_name: DataFrame[time, open,
   high, low, close, tick_volume]}` with `time` parsed from the unix `time` column to datetime.
   Optional `start`/`end` (datetime) filter each TF by `time`. A missing CSV raises a clear
   `RuntimeError` naming the expected path.

2. **Baked symbol-info constants** (captured from MT5 on 2026-06-08, this broker):
   ```
   SYMBOL_INFO_CONST = {
       "BTCUSDm": {"point": 0.01,  "tick_value": 0.01},
       "XAUUSDm": {"point": 0.001, "tick_value": 0.1},
   }
   ```
   `load_symbol_info(symbol, source)` returns the MT5 values when `source == "mt5"`, else the baked
   constant (CSV mode, no MT5). Unknown symbol in CSV mode → clear `RuntimeError`.

3. **CLI switch** (argparse): `--data-source {mt5,csv}` default `mt5` (non-breaking — existing
   invocations are byte-for-byte unchanged), `--data-dir` default `data/history`. Date range in CSV
   mode reuses `--months`: `--months 0` = full file (no filter); `> 0` = keep only the last N months
   (relative to each file's last bar, so it works offline without "now"). `mt5` mode ignores
   `--data-dir` and keeps today's `--months` behavior.

### Data flow

```
CLI --data-source csv
  -> load_data_csv(symbol, data_dir, start, end)      # candles from disk
  -> load_symbol_info(symbol, "csv") = SYMBOL_INFO_CONST[symbol]
  -> existing scenario engine (UNCHANGED)
  -> metrics + --export CSV
```

### Error handling

- Missing CSV file → `RuntimeError(f"CSV not found: {path}")`.
- Empty CSV → `RuntimeError` naming symbol+TF.
- Unknown symbol constants in CSV mode → `RuntimeError`.
- `mt5` mode behavior and errors are unchanged.

## Testing (TDD)

- `load_data_csv`: returns the 5 TFs with exactly `[time, open, high, low, close, tick_volume]`;
  `time` is datetime; `start`/`end` filter rows; missing file raises with the path in the message.
  (Use a tiny temp CSV fixture, not the 41 MB real files.)
- `load_symbol_info(symbol, "csv")`: returns the baked constant for BTC and XAU; unknown symbol
  raises.
- A `--months` filter helper test: N>0 keeps only the trailing N months relative to the last bar;
  N==0 keeps all rows.

## Run (after implementation)

Run baseline and st3 scenarios over the full CSV range and export, e.g.
`python backtest.py --scenario s3a --data-source csv --months 0 --export s3a_7y.csv` (exact scenario
list resolved in the plan). Compare against the 6-month results as a sanity check on direction.

## Open items

- Exact scenario set to run for the 7-year validation (resolved in the plan).
- BTC history starts 2018-12, XAU 2018-06 (~7.5–8 yr) — the broker has no earlier BTC/XAU data; "7
  years" is satisfied within that span.
