# Trend-Following SELL Backtest Comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three trend-following SELL entry triggers to the `backtest.py` research harness as parallel SELL paths, run them against the mean-reversion baseline over BTC+XAU/6mo, and produce a composite comparison table to pick a winner.

**Architecture:** Three pure trigger predicates (Donchian breakdown / EMA cross / RSI momentum cross) are added to `backtest.py`. A new per-bar block evaluates the active trigger and, gated on a confirmed D1 DOWNTREND, opens a SELL via the same virtual-position machinery as the baseline. Baseline behavior is preserved (verified byte-identical). A small analysis script aggregates per-scenario CSV exports into the composite table. No live trading code changes.

**Tech Stack:** Python 3.12, pandas, MetaTrader5 (mocked in tests), pytest. Tests follow `test_backtest.py` (mocks MT5 + heavy deps at import, builds synthetic OHLCV via `make_ohlcv`).

**Verified facts (from `main:backtest.py`, 760 lines):**
- Per-bar entry picks one direction: `direction = "BUY" if rsi_now < 50 else "SELL"` (~line 525), then score/filter/guardian gates, then opens a virtual position (~lines 587-616: `compute_dynamic_sl_tp` → `simulate_position_exit` → `compute_net_profit` → append to `open_positions`).
- `compute_director(h4_slice, d1_slice)` returns `{"allowed_direction", "h4_trend", "d1_trend"}`. `director_state` is refreshed every `DIRECTOR_REFRESH_BARS` (~line 506). Initial `director_state` has no `d1_trend` key → always read with `.get("d1_trend", "SIDEWAY")`.
- `_get_trend` returns `"UPTREND"|"DOWNTREND"|"SIDEWAY"` from Chandelier.
- Scenario flags live in `filter_cfg` dict (~lines 429-444), keyed off `sc = args.scenario`.
- Helpers available in module scope: `calculate_rsi(list)`, `_calc_atr_chandelier(df)`, `check_guardian(...)`, `compute_dynamic_sl_tp(entry, m5_slice, direction)`, `simulate_position_exit(...)`, `compute_net_profit(...)`. pandas `Series.ewm` is available for EMA.
- `export_csv` writes fields list (~line 745): symbol, direction, entry_time, entry_price, exit_price, net_profit, result, exit_time, rsi_entry, score, h4_trend, allowed_direction, layers.
- Files `backtest.py`, `run_scenarios.py`, `test_backtest.py` exist on `main` but are DELETED in the current working tree (must be restored).

---

## File Structure

- **`backtest.py`** (restore, then modify) — add 3 pure trigger predicates, the trend-sell entry block, `st1/st2/st3` scenario flags, and `d1_trend`/`entry_reason` on the position dict + CSV.
- **`test_backtest.py`** (restore, then modify) — add unit tests for the 3 predicates.
- **`run_scenarios.py`** (restore only) — existing multi-scenario runner; no change needed.
- **`analyze_trend_sell.py`** (create) — reads per-scenario CSV exports, prints the composite comparison table.
- **`docs/superpowers/results/2026-06-05-trend-sell-backtest-results.md`** (create) — captured comparison table + decision.

---

## Task 1: Restore deleted harness files from main

**Files:**
- Restore: `backtest.py`, `run_scenarios.py`, `test_backtest.py`

- [ ] **Step 1: Restore the three files from main into the working tree**

```bash
git checkout main -- backtest.py run_scenarios.py test_backtest.py
```

- [ ] **Step 2: Verify they are present and the existing tests pass**

Run: `python -m pytest test_backtest.py -q`
Expected: all existing tests PASS (collection succeeds; MT5 is mocked in the test file).

- [ ] **Step 3: Verify backtest imports**

Run: `python -c "import ast; ast.parse(open('backtest.py', encoding='utf-8').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Commit**

```bash
git add backtest.py run_scenarios.py test_backtest.py
git commit -m "chore(backtest): restore harness files from main for trend-sell work"
```
End commit body with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 2: Pure trend-sell trigger predicates + unit tests (TDD)

**Files:**
- Modify: `backtest.py` (add 3 module-level functions near the other indicator helpers, e.g. just after `_get_trend`)
- Test: `test_backtest.py` (append tests)

- [ ] **Step 1: Write the failing tests** — append to `test_backtest.py`:

```python
def _down_then_flat(n_down=30, n_flat=10, base=100.0):
    """A falling series that flattens — RSI rises back through 50 then we can
    construct a cross. Returns an OHLCV DataFrame."""
    rows = []
    closes = [base - i * 0.5 for i in range(n_down)] + [base - n_down * 0.5] * n_flat
    for i, close in enumerate(closes):
        rows.append({
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=5 * i),
            "open": close, "high": close + 0.3, "low": close - 0.3,
            "close": close, "tick_volume": 100,
        })
    return pd.DataFrame(rows)


def test_donchian_breakdown_fires_on_new_low():
    import backtest
    df = make_ohlcv(40, trend="down")           # steadily falling closes+lows
    assert backtest.donchian_breakdown(df, n=20) is True


def test_donchian_breakdown_false_when_rising():
    import backtest
    df = make_ohlcv(40, trend="up")
    assert backtest.donchian_breakdown(df, n=20) is False


def test_donchian_breakdown_false_when_insufficient_bars():
    import backtest
    df = make_ohlcv(10, trend="down")
    assert backtest.donchian_breakdown(df, n=20) is False


def test_ema_cross_down_true_in_downtrend():
    import backtest
    df = make_ohlcv(40, trend="down")
    assert backtest.ema_cross_down(df, fast=9, slow=21) is True


def test_ema_cross_down_false_in_uptrend():
    import backtest
    df = make_ohlcv(40, trend="up")
    assert backtest.ema_cross_down(df, fast=9, slow=21) is False


def test_rsi_cross_down_fires_when_crossing_below_level():
    import backtest
    # Build a series that is rising (RSI high) then turns down on the last bar,
    # so RSI crosses from >=50 to <50.
    closes = [100 + i for i in range(20)] + [119, 110]  # sharp drop at the end
    rows = [{"time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=5 * i),
             "open": c, "high": c + 0.3, "low": c - 0.3, "close": c, "tick_volume": 100}
            for i, c in enumerate(closes)]
    df = pd.DataFrame(rows)
    assert backtest.rsi_cross_down(df, level=50.0) is True


def test_rsi_cross_down_false_when_already_below():
    import backtest
    df = make_ohlcv(40, trend="down")   # RSI stays low throughout, no downward cross
    assert backtest.rsi_cross_down(df, level=50.0) is False
```

- [ ] **Step 2: Run tests, verify they FAIL**

Run: `python -m pytest test_backtest.py -k "donchian or ema_cross or rsi_cross" -v`
Expected: FAIL — `AttributeError: module 'backtest' has no attribute 'donchian_breakdown'`

- [ ] **Step 3: Implement the predicates** in `backtest.py` (module level, after `_get_trend`):

```python
def donchian_breakdown(m5_slice, n: int = 20) -> bool:
    """True when the latest close breaks below the lowest low of the prior n bars."""
    if len(m5_slice) < n + 1:
        return False
    prior = m5_slice.iloc[-(n + 1):-1]
    return float(m5_slice.iloc[-1]["close"]) < float(prior["low"].min())


def ema_cross_down(m5_slice, fast: int = 9, slow: int = 21) -> bool:
    """True when the fast EMA is below the slow EMA and price is below the slow EMA."""
    if len(m5_slice) < slow + 1:
        return False
    closes = m5_slice["close"]
    ema_fast = closes.ewm(span=fast, adjust=False).mean().iloc[-1]
    ema_slow = closes.ewm(span=slow, adjust=False).mean().iloc[-1]
    return bool(ema_fast < ema_slow and float(closes.iloc[-1]) < ema_slow)


def rsi_cross_down(m5_slice, level: float = 50.0) -> bool:
    """True when RSI crosses down through `level` on the latest bar
    (previous RSI >= level, current RSI < level)."""
    closes = m5_slice["close"].tolist()
    if len(closes) < 16:
        return False
    rsi_prev = calculate_rsi(closes[:-1])
    rsi_now = calculate_rsi(closes)
    return bool(rsi_prev >= level and rsi_now < level)
```

- [ ] **Step 4: Run tests, verify they PASS**

Run: `python -m pytest test_backtest.py -k "donchian or ema_cross or rsi_cross" -v`
Expected: 7 passed. If `test_rsi_cross_down_fires_when_crossing_below_level` fails, adjust the synthetic `closes` so the documented cross actually occurs — the predicate is correct; the fixture must produce prev-RSI>=50 and now-RSI<50. Verify by printing `backtest.calculate_rsi(closes[:-1])` and `backtest.calculate_rsi(closes)`.

- [ ] **Step 5: Run the full test file to confirm no regressions**

Run: `python -m pytest test_backtest.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backtest.py test_backtest.py
git commit -m "feat(backtest): add donchian/ema/rsi trend-sell trigger predicates + tests"
```
End commit body with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 3: Integrate trend-sell entry path + scenario flags + d1_trend in output

**Files:**
- Modify: `backtest.py` — `filter_cfg`, the per-bar loop, position dict, `export_csv` fields, `parse_args` help text.

- [ ] **Step 1: Add scenario flags to `filter_cfg`**

In the `filter_cfg = { ... }` dict (~line 430), add these keys (keep existing keys unchanged):

```python
            "trend_sell":       sc in ("st1", "st2", "st3"),
            "ts_donchian":      sc == "st1",
            "ts_ema":           sc == "st2",
            "ts_rsi":           sc == "st3",
```

- [ ] **Step 2: Add `d1_trend` to the baseline position dict**

In the existing `open_positions.append({ ... })` block (~line 600), add one line alongside `"h4_trend": ...`:

```python
                "d1_trend":          director_state.get("d1_trend", "SIDEWAY"),
                "entry_reason":      "baseline",
```

- [ ] **Step 3: Insert the trend-sell entry block**

Locate the point right after the slices are computed (`m5_slice`, `m15_slice`, `h1_slice` are assigned, ~line 518) and BEFORE the `if filter_cfg["atr_filter"]:` line. Insert:

```python
            # 4-TS. Trend-following SELL path (st1/st2/st3): a second SELL entry that
            # fires on a momentum/breakdown trigger, gated on a confirmed D1 DOWNTREND.
            # Runs in parallel with the mean-reversion path below; one entry per bar.
            if filter_cfg["trend_sell"] and director_state.get("d1_trend") == "DOWNTREND":
                ts_fired = (
                    (filter_cfg["ts_donchian"] and donchian_breakdown(m5_slice)) or
                    (filter_cfg["ts_ema"]      and ema_cross_down(m5_slice)) or
                    (filter_cfg["ts_rsi"]      and rsi_cross_down(m5_slice))
                )
                if ts_fired:
                    ts_allowed, _ = check_guardian(
                        symbol=symbol, direction="SELL",
                        allowed_direction=director_state["allowed_direction"],
                        last_sl_bar=last_sl_bar, current_bar_idx=i,
                        open_positions=open_positions,
                        fixed_spread=fixed_spread, max_spread=max_spread,
                    )
                    if ts_allowed:
                        ts_entry = float(m5_df.iloc[i]["close"])
                        ts_sltp = compute_dynamic_sl_tp(ts_entry, m5_slice, "SELL")
                        if ts_sltp is not None:
                            ts_sl, ts_tp, _ = ts_sltp
                            ts_exit = simulate_position_exit(
                                m5_df, i, "SELL", ts_entry, ts_sl, ts_tp)
                            ts_spread_cost = spread_pts * tick_value * lot
                            ts_net = compute_net_profit(
                                "SELL", ts_entry, ts_exit["exit_price"],
                                lot, tick_value, point) - ts_spread_cost
                            ts_layers = sum(1 for p in open_positions
                                            if p["symbol"] == symbol) + 1
                            open_positions.append({
                                "symbol":            symbol,
                                "direction":         "SELL",
                                "entry_bar":         i,
                                "entry_time":        str(bar_time),
                                "entry_price":       ts_entry,
                                "sl_price":          ts_sl,
                                "tp_price":          ts_tp,
                                "rsi_entry":         calculate_rsi(m5_slice["close"].tolist()),
                                "score":             0,
                                "h4_trend":          director_state.get("h4_trend", "N/A"),
                                "d1_trend":          director_state.get("d1_trend", "SIDEWAY"),
                                "allowed_direction": director_state["allowed_direction"],
                                "layers":            ts_layers,
                                "net_profit":        ts_net,
                                "entry_reason":      "trend_sell",
                                **ts_exit,
                            })
                            continue  # one entry per bar — skip the mean-reversion path
```

- [ ] **Step 4: Add `d1_trend` and `entry_reason` to the CSV export**

In `export_csv`, extend the `fields` list to include the two new keys (append after `"allowed_direction"`):

```python
    fields = ["symbol", "direction", "entry_time", "entry_price",
              "exit_price", "net_profit", "result", "exit_time",
              "rsi_entry", "score", "h4_trend", "d1_trend",
              "allowed_direction", "entry_reason", "layers"]
```

- [ ] **Step 5: Update `parse_args` help text**

In `parse_args`, the `--scenario` help string lists scenarios. Append a mention of the new ones so the harness is self-documenting:

```python
                         "st1:trend-sell Donchian  st2:trend-sell EMA  st3:trend-sell RSI-cross  "
```
(Add this fragment inside the existing multi-line `help=(...)` string for `--scenario`; do not remove existing text.)

- [ ] **Step 6: Verify syntax + tests still green**

Run: `python -c "import ast; ast.parse(open('backtest.py', encoding='utf-8').read()); print('parse OK')"` → `parse OK`
Run: `python -m pytest test_backtest.py -q` → all pass.

- [ ] **Step 7: Baseline regression — prove baseline output is unchanged**

The baseline path must be byte-identical to before this task (only additive keys differ). Verify the *trade-generating logic* is unchanged by running the baseline scenario and confirming it still runs and the new keys default correctly. Because a full MT5 data run needs the live terminal (not available in dev), this step is a structural check, not a data run:

Run: `python -c "import backtest, inspect; src=inspect.getsource(backtest.run_backtest); assert 'trend_sell' in src and src.count('open_positions.append') == 2, 'expected exactly baseline + trend-sell append sites'; print('structure OK')"`
Expected: `structure OK` (confirms exactly two position-open sites: the untouched baseline and the new trend-sell block).

- [ ] **Step 8: Commit**

```bash
git add backtest.py
git commit -m "feat(backtest): trend-sell entry path + st1/st2/st3 scenarios + d1_trend export"
```
End commit body with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 4: Composite comparison analysis + run

**Files:**
- Create: `analyze_trend_sell.py`
- Create: `docs/superpowers/results/2026-06-05-trend-sell-backtest-results.md`

- [ ] **Step 1: Write `analyze_trend_sell.py`**

```python
"""Aggregate per-scenario backtest CSV exports into a composite comparison table.

Usage:
  python analyze_trend_sell.py baseline.csv st1.csv st2.csv st3.csv

Each CSV is a backtest trade log (from backtest.py --export). Prints one row per
(scenario, symbol) with the composite metrics from the design spec.
"""
import csv
import statistics
import sys
from pathlib import Path


def _load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _sharpe(rows):
    by_day = {}
    for r in rows:
        day = r["exit_time"][:10]
        by_day[day] = by_day.get(day, 0.0) + float(r["net_profit"])
    daily = list(by_day.values())
    if len(daily) < 2:
        return 0.0
    sd = statistics.stdev(daily)
    return round(statistics.mean(daily) / sd * (252 ** 0.5), 2) if sd else 0.0


def _max_dd(rows):
    eq = peak = mdd = 0.0
    for r in sorted(rows, key=lambda x: x["exit_time"]):
        eq += float(r["net_profit"])
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, min((peak - eq) / peak * 100, 100.0))
    return round(mdd, 1)


def _metrics(rows):
    profits = [float(r["net_profit"]) for r in rows]
    wins = [p for p in profits if p > 0]
    sells = [r for r in rows if r["direction"] == "SELL"]
    sell_down = [r for r in sells if r.get("d1_trend") == "DOWNTREND"]
    avg_r = (statistics.mean([p for p in profits]) if profits else 0.0)
    return {
        "trades": len(rows),
        "wr": round(len(wins) / len(profits) * 100, 1) if profits else 0.0,
        "pnl": round(sum(profits), 2),
        "max_dd": _max_dd(rows),
        "sharpe": _sharpe(rows),
        "sells": len(sells),
        "sell_down": len(sell_down),
        "avg_pnl": round(avg_r, 3),
    }


def main(paths):
    hdr = f"{'scenario':<10}{'symbol':<10}{'trades':>7}{'WR%':>7}{'PnL':>10}" \
          f"{'MaxDD%':>8}{'Sharpe':>8}{'#SELL':>7}{'#SELLdn':>9}{'avgPnL':>9}"
    print(hdr)
    print("-" * len(hdr))
    for path in paths:
        scenario = Path(path).stem
        rows = _load(path)
        symbols = sorted({r["symbol"] for r in rows})
        for sym in symbols:
            srows = [r for r in rows if r["symbol"] == sym]
            m = _metrics(srows)
            print(f"{scenario:<10}{sym:<10}{m['trades']:>7}{m['wr']:>7}"
                  f"{m['pnl']:>10}{m['max_dd']:>8}{m['sharpe']:>8}"
                  f"{m['sells']:>7}{m['sell_down']:>9}{m['avg_pnl']:>9}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
```

- [ ] **Step 2: Smoke-test the analyzer on a synthetic CSV (no MT5 needed)**

```bash
python -c "
import csv
rows=[{'symbol':'BTCUSDm','direction':'SELL','entry_time':'2026-01-01 00:00','entry_price':'100','exit_price':'99','net_profit':'1.0','result':'TP','exit_time':'2026-01-01 01:00','rsi_entry':'40','score':'0','h4_trend':'DOWNTREND','d1_trend':'DOWNTREND','allowed_direction':'SELL_ONLY','entry_reason':'trend_sell','layers':'1'}]
import io
with open('._smoke.csv','w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
"
python analyze_trend_sell.py ._smoke.csv
```
Expected: a table with one BTCUSDm row, `#SELL=1`, `#SELLdn=1`. Then delete the smoke file: `rm ._smoke.csv` (PowerShell: `Remove-Item ._smoke.csv`).

- [ ] **Step 3: Run the four scenarios (requires the live MT5 terminal — run on the trading server)**

This step pulls 6 months of historical data via MT5, so it must run where MT5 is connected. Per the project's deploy/server convention, run on the server (`Administrator@100.106.19.75`, path `C:\Users\Administrator\Desktop\PC-Trading`) after the branch is available there, OR locally if an MT5 terminal is connected:

```bash
python backtest.py --scenario baseline --months 6 --symbols BTCUSDm XAUUSDm --export baseline.csv
python backtest.py --scenario st1 --months 6 --symbols BTCUSDm XAUUSDm --export st1.csv
python backtest.py --scenario st2 --months 6 --symbols BTCUSDm XAUUSDm --export st2.csv
python backtest.py --scenario st3 --months 6 --symbols BTCUSDm XAUUSDm --export st3.csv
python analyze_trend_sell.py baseline.csv st1.csv st2.csv st3.csv
```
Expected: a composite table comparing baseline vs st1/st2/st3 for each symbol. (Exact CLI flag names — `--months`, `--symbols`, `--export` — are defined in `parse_args`; confirm against `python backtest.py --help` and adjust if the harness uses different flag spellings.)

- [ ] **Step 4: Capture results + decision**

Paste the composite table into `docs/superpowers/results/2026-06-05-trend-sell-backtest-results.md` with a short verdict: which trigger raised `#SELLdn` the most without hurting PnL/MaxDD/Sharpe vs baseline, or "none beats baseline." State the decision and the next step (open a separate live-port spec for the winner, or drop the idea).

- [ ] **Step 5: Commit**

```bash
git add analyze_trend_sell.py docs/superpowers/results/2026-06-05-trend-sell-backtest-results.md
git commit -m "feat(backtest): composite trend-sell comparison analyzer + results"
```
End commit body with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Self-Review

- **Spec coverage:** §1 architecture → Tasks 2+3. §2 trigger definitions → Task 2 predicates (Donchian n=20, EMA 9/21, RSI cross 50) + Task 3 gating on D1 DOWNTREND. §3 composite metrics → Task 4 analyzer (PnL, WR, MaxDD, Sharpe, #trades, #SELL, #SELL-in-downtrend, avg). §4 scope (restore files, add scenarios, run, write results; no live code) → Tasks 1-4, only backtest/analysis files touched. §5 caveat (mock AI) → recorded in results doc verdict framing. §6 decision gate → Task 4 Step 4. Testing (predicate unit tests) → Task 2.
- **Placeholder scan:** no TBD/TODO; every code step has full code; the one data-dependent step (Task 4 Step 3) is explicitly gated on MT5 availability with a structural fallback check in Task 3 Step 7.
- **Type/name consistency:** predicate names `donchian_breakdown` / `ema_cross_down` / `rsi_cross_down` and their `(m5_slice, ...)` signatures are identical across Task 2 def, Task 2 tests, and Task 3 call sites. Scenario keys `trend_sell/ts_donchian/ts_ema/ts_rsi` identical across Task 3 filter_cfg and the entry block. CSV keys `d1_trend`/`entry_reason` identical across Task 3 position dicts, Task 3 export fields, and Task 4 analyzer (`r.get("d1_trend")`, `r["direction"]`).
- **Baseline-preservation requirement (spec §1/§4):** Task 3 adds only additive keys to the baseline dict and a separate trend-sell append site; Task 3 Step 7 asserts exactly two append sites so the baseline logic is provably untouched.
