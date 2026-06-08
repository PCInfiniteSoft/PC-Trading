# Backtest CSV Data Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `backtest.py` run its existing scenarios over the pre-fetched `data/history/` CSVs with no live MT5 connection, then run a 7-year validation.

**Architecture:** Two swappable seams already exist — `load_data(symbol, months)` for candles and `load_symbol_info(symbol)` for tick geometry. Add CSV-backed implementations + baked symbol-info constants behind a `--data-source {mt5,csv}` switch. The bar-by-bar engine and scenarios are untouched; `mt5` stays the default so existing invocations are unchanged.

**Tech Stack:** Python 3.12, pandas, argparse, pytest. CSVs written by `fetch_history.py` (columns: `time, open, high, low, close, tick_volume, spread, real_volume, datetime`).

**Spec:** `docs/superpowers/specs/2026-06-08-backtest-csv-source-design.md`

---

## Context for the implementer (read before Task 1)

- `backtest.py:30` `load_data(symbol, months) -> {tf_name: DataFrame[time, open, high, low, close, tick_volume]}` — fetches from MT5; `time` is datetime. `TF_MAP` (L21) = `{"M5","M15","H1","H4","D1"}`.
- `backtest.py:45` `load_symbol_info(symbol) -> {"point":..., "tick_value":...}` — from `mt5.symbol_info`.
- `backtest.py:371` `main()` → `init_mt5()` (which calls `mt5.initialize`, L365) → `run_backtest(args)` → `print_report` → `export_csv` → `mt5.shutdown()` in a `finally`.
- `backtest.py` ~L430-439 `run_backtest`: per symbol, `data = load_data(symbol, args.months); sym_info = load_symbol_info(symbol)`.
- `parse_args` (L331) uses argparse; `--months` default 3.
- Symbol-info constants captured from MT5 on 2026-06-08 (this broker):
  `BTCUSDm point=0.01 tick_value=0.01`, `XAUUSDm point=0.001 tick_value=0.1`.
- CSV history span: BTC 2018-12-29→2026-06-07, XAU 2018-06-28→2026-06-05.
- **Test mock pattern:** importing `backtest` imports `MetaTrader5`. New test file installs the mt5 mock via `sys.modules.setdefault` (mirror `test_trend_sell_wiring.py` lines 12-24). `load_data_csv` and the constants need no live mt5 at runtime — they read disk / a dict.

---

## File structure

- `backtest.py` — add `load_data_csv`, `_filter_trailing_months`, `SYMBOL_INFO_CONST`; change `load_symbol_info` to take a `source`; add CLI flags; branch data loading + MT5 init on `args.data_source`. (Modify)
- `test_backtest_csv.py` — unit tests for the CSV loader, trailing-month filter, and symbol-info source. (Create)

---

## Task 1: `_filter_trailing_months` helper

**Files:**
- Modify: `backtest.py` (add helper near `load_data`)
- Test: `test_backtest_csv.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test_backtest_csv.py`:

```python
"""Unit tests for the CSV data source (2026-06-08). mt5 mocked before importing backtest."""
import sys
from unittest.mock import MagicMock
import pandas as pd
import pytest

_mt5 = MagicMock()
_mt5.TIMEFRAME_M5, _mt5.TIMEFRAME_M15 = 5, 15
_mt5.TIMEFRAME_H1, _mt5.TIMEFRAME_H4, _mt5.TIMEFRAME_D1 = 16385, 16388, 16408
sys.modules.setdefault('MetaTrader5', _mt5)

import backtest  # noqa: E402


def _df(times):
    return pd.DataFrame({
        "time": pd.to_datetime(times),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1,
    })


def test_filter_zero_months_keeps_all():
    df = _df(["2018-01-01", "2020-01-01", "2026-01-01"])
    out = backtest._filter_trailing_months(df, 0)
    assert len(out) == 3


def test_filter_trailing_keeps_only_recent():
    # last bar 2026-06-01; 2 months back = ~2026-04-01 cutoff
    df = _df(["2026-01-01", "2026-05-01", "2026-06-01"])
    out = backtest._filter_trailing_months(df, 2)
    assert list(out["time"].dt.strftime("%Y-%m-%d")) == ["2026-05-01", "2026-06-01"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute '_filter_trailing_months'`

- [ ] **Step 3: Implement the helper in `backtest.py`** (add directly above `load_data`, after `TF_MAP`)

```python
def _filter_trailing_months(df, months):
    """Keep only rows within the last `months` months of the file's last bar.
    months == 0 → return df unchanged (full history). Works offline (no 'now')."""
    if months <= 0 or df.empty:
        return df
    cutoff = df["time"].max() - pd.Timedelta(days=months * 31)
    return df[df["time"] >= cutoff].reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add backtest.py test_backtest_csv.py
git commit -m "feat(backtest): trailing-month filter helper for CSV source"
```

---

## Task 2: `load_data_csv`

**Files:**
- Modify: `backtest.py` (add `load_data_csv` below `load_data`)
- Test: `test_backtest_csv.py`

- [ ] **Step 1: Write the failing test**

Append to `test_backtest_csv.py`:

```python
def _write_csv(path, times):
    pd.DataFrame({
        "time": [int(pd.Timestamp(t).timestamp()) for t in times],
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "tick_volume": 10, "spread": 0, "real_volume": 0,
        "datetime": times,
    }).to_csv(path, index=False)


def test_load_data_csv_returns_all_tfs(tmp_path):
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        _write_csv(tmp_path / f"BTCUSDm_{tf}.csv", ["2020-01-01 00:00", "2020-01-01 00:05"])
    data = backtest.load_data_csv("BTCUSDm", str(tmp_path))
    assert set(data.keys()) == {"M5", "M15", "H1", "H4", "D1"}
    assert list(data["M5"].columns) == ["time", "open", "high", "low", "close", "tick_volume"]
    assert str(data["M5"]["time"].dtype).startswith("datetime64")
    assert len(data["M5"]) == 2


def test_load_data_csv_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError, match="CSV not found"):
        backtest.load_data_csv("BTCUSDm", str(tmp_path))


def test_load_data_csv_applies_trailing_months(tmp_path):
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        _write_csv(tmp_path / f"BTCUSDm_{tf}.csv",
                   ["2025-01-01 00:00", "2026-05-01 00:00", "2026-06-01 00:00"])
    data = backtest.load_data_csv("BTCUSDm", str(tmp_path), months=2)
    assert len(data["M5"]) == 2  # only the two 2026 bars survive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py::test_load_data_csv_returns_all_tfs -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute 'load_data_csv'`

- [ ] **Step 3: Implement `load_data_csv` in `backtest.py`** (directly below `load_data`)

```python
import os

def load_data_csv(symbol: str, data_dir: str, months: int = 0) -> dict:
    """Load candles for all TFs from {data_dir}/{symbol}_{TF}.csv (written by
    fetch_history.py). Returns the same shape as load_data:
    {tf_name: DataFrame[time, open, high, low, close, tick_volume]} with `time`
    as datetime. `months > 0` keeps only the trailing N months of each TF."""
    result = {}
    for tf_name in TF_MAP:
        path = os.path.join(data_dir, f"{symbol}_{tf_name}.csv")
        if not os.path.exists(path):
            raise RuntimeError(f"CSV not found: {path}")
        df = pd.read_csv(path)
        if df.empty:
            raise RuntimeError(f"Empty CSV for {symbol} {tf_name}: {path}")
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].reset_index(drop=True)
        result[tf_name] = _filter_trailing_months(df, months)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py -v`
Expected: PASS (5 total)

- [ ] **Step 5: Commit**

```bash
git add backtest.py test_backtest_csv.py
git commit -m "feat(backtest): load_data_csv reads data/history CSVs"
```

---

## Task 3: Baked symbol-info constants + `source` param

**Files:**
- Modify: `backtest.py` (`load_symbol_info`)
- Test: `test_backtest_csv.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_symbol_info_csv_constants():
    btc = backtest.load_symbol_info("BTCUSDm", source="csv")
    assert btc == {"point": 0.01, "tick_value": 0.01}
    xau = backtest.load_symbol_info("XAUUSDm", source="csv")
    assert xau == {"point": 0.001, "tick_value": 0.1}


def test_symbol_info_csv_unknown_raises():
    with pytest.raises(RuntimeError, match="No baked symbol info"):
        backtest.load_symbol_info("EURUSDm", source="csv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py::test_symbol_info_csv_constants -v`
Expected: FAIL — `TypeError: load_symbol_info() got an unexpected keyword argument 'source'`

- [ ] **Step 3: Modify `load_symbol_info` in `backtest.py`**

Replace the existing `load_symbol_info` (L45-50) with:

```python
SYMBOL_INFO_CONST = {
    "BTCUSDm": {"point": 0.01,  "tick_value": 0.01},
    "XAUUSDm": {"point": 0.001, "tick_value": 0.1},
}

def load_symbol_info(symbol: str, source: str = "mt5") -> dict:
    """Point size + tick value. source='mt5' queries the terminal; source='csv'
    returns the baked constant (captured from MT5 2026-06-08) so backtests run
    with no live connection."""
    if source == "csv":
        info = SYMBOL_INFO_CONST.get(symbol)
        if info is None:
            raise RuntimeError(f"No baked symbol info for {symbol} (add to SYMBOL_INFO_CONST)")
        return dict(info)
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol info not found for {symbol}")
    return {"point": info.point, "tick_value": info.trade_tick_value}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py -v`
Expected: PASS (7 total)

- [ ] **Step 5: Commit**

```bash
git add backtest.py test_backtest_csv.py
git commit -m "feat(backtest): baked symbol-info constants for CSV mode"
```

---

## Task 4: CLI flags + dispatch in `main`/`run_backtest`

**Files:**
- Modify: `backtest.py` (`parse_args`, `main`, `run_backtest`)
- Test: `test_backtest_csv.py`

- [ ] **Step 1: Write the failing test**

Append (tests the parsed defaults + that csv mode is recognized):

```python
def test_parse_args_data_source_default_mt5():
    sys.argv = ["backtest.py"]
    args = backtest.parse_args()
    assert args.data_source == "mt5"
    assert args.data_dir == "data/history"


def test_parse_args_data_source_csv():
    sys.argv = ["backtest.py", "--data-source", "csv", "--data-dir", "x/y"]
    args = backtest.parse_args()
    assert args.data_source == "csv"
    assert args.data_dir == "x/y"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_backtest_csv.py::test_parse_args_data_source_default_mt5 -v`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'data_source'`

- [ ] **Step 3: Add the CLI flags in `parse_args`** (after the `--scenario` argument, before `return p.parse_args()`)

```python
    p.add_argument("--data-source", choices=["mt5", "csv"], default="mt5",
                   help="Candle source: live MT5 (default) or pre-fetched CSVs")
    p.add_argument("--data-dir", type=str, default="data/history", metavar="DIR",
                   help="Directory of {symbol}_{TF}.csv files for --data-source csv")
```

- [ ] **Step 4: Skip MT5 init in csv mode — edit `main`**

```python
def main():
    args = parse_args()
    use_mt5 = args.data_source == "mt5"
    if use_mt5 and not init_mt5():
        sys.exit(1)
    try:
        trades = run_backtest(args)
        print_report(trades, args)
        if args.export:
            export_csv(trades, args.export)
    finally:
        if use_mt5:
            mt5.shutdown()
```

- [ ] **Step 5: Dispatch data loading in `run_backtest`** — replace the `else:` load block (the `load_data(symbol, args.months)` + `load_symbol_info(symbol)` lines, ~L437-439)

```python
        else:
            print(f"[INFO] Loading data for {symbol} ({args.data_source}) ...")
            if args.data_source == "csv":
                data     = load_data_csv(symbol, args.data_dir, args.months)
                sym_info = load_symbol_info(symbol, source="csv")
            else:
                data     = load_data(symbol, args.months)
                sym_info = load_symbol_info(symbol, source="mt5")
```

Also update the cached-branch fallback (~L435) `load_symbol_info(symbol)` → `load_symbol_info(symbol, source=args.data_source)`.

- [ ] **Step 6: Run the full suite to verify nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all existing + 9 new in test_backtest_csv.py)

- [ ] **Step 7: Commit**

```bash
git add backtest.py test_backtest_csv.py
git commit -m "feat(backtest): --data-source csv wiring (mt5 default, non-breaking)"
```

---

## Task 5: Run the 7-year validation

**Files:** none (execution + results doc)

- [ ] **Step 1: Run baseline + st3 scenarios over full CSV history**

```bash
.venv/Scripts/python.exe backtest.py --scenario s3a --data-source csv --months 0 --capital 300 --lot 0.03 --spread 0.20 --export s3a_7y.csv
.venv/Scripts/python.exe backtest.py --scenario s3a_st3 --data-source csv --months 0 --capital 300 --lot 0.03 --spread 0.20 --export s3a_st3_7y.csv
```
Expected: completes with no MT5 connection; prints a per-symbol report; writes the two CSVs.

- [ ] **Step 2: Record results**

Write `docs/superpowers/results/2026-06-08-7year-backtest-results.md` with the per-symbol Net PnL / MaxDD / Sharpe / WinRate for each scenario and a one-paragraph read vs the 6-month numbers (sanity: same direction, st3 still unlocks XAU shorts). Note the history span (BTC 2018-12+, XAU 2018-06+).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/results/2026-06-08-7year-backtest-results.md
git commit -m "docs: 7-year CSV backtest results (s3a vs s3a_st3)"
```

---

## Self-review notes (spec coverage)

- **`load_data_csv` (CSV candles, same shape)** → Task 2.
- **Baked symbol-info constants + source switch** → Task 3.
- **`--data-source` / `--data-dir` CLI, mt5 default non-breaking** → Task 4 Steps 3-4.
- **`--months 0` full / N trailing** → Task 1 + Task 2 (filter applied in loader).
- **Skip MT5 init/shutdown in csv mode** → Task 4 Step 4.
- **Run 7-year validation + record** → Task 5.
- **Error handling (missing/empty CSV, unknown symbol)** → Task 2 + Task 3 tests + impl.
- **Out of scope (SP-C buckets)** → not in any task, by design.

Type consistency: `load_symbol_info(symbol, source=...)`, `load_data_csv(symbol, data_dir, months)`, `_filter_trailing_months(df, months)`, `SYMBOL_INFO_CONST` — names identical across all tasks.
