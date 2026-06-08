# SP-A Phase 1 — Strategy Module Extraction (Backtest-Side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract today's backtest entry/exit decision logic into a shared, IO-free `strategy/` package driven by an injected scorer, and route `backtest.py` through it — with byte-identical trade output as the gate.

**Architecture:** Introduce a pure `strategy/` package (`MarketContext`, `AnalystResult`, `Scorer` protocol, `Decision`, `Strategy` protocol) plus one config-driven `ConfigStrategy` that reproduces the current scenario behavior exactly. `backtest.run_backtest` builds a `MarketContext` per bar and calls `strategy.evaluate_entry/exit` instead of inlining the decision logic; the existing `compute_analyst_score` is wrapped as `MockScorer` and injected. No live (`trade_manager`) changes — that is Phase 2.

**Tech Stack:** Python 3.12, pandas, pytest. No new dependencies.

---

## Scope

In scope (Phase 1 only):
- `strategy/` package: dataclasses + protocols + `MockScorer` + `ConfigStrategy`.
- Rewire `backtest.py` to drive decisions through `ConfigStrategy`.
- Regression gate: `baseline`, `s3a`, `st3`, `s3a_st3` trade CSVs byte-identical to pre-refactor.

Out of scope: any `trade_manager.py` / live changes (Phase 2), BTC/XAU split (Phase 3),
SP-B/SP-C. No behavior change — this is a structural extraction.

## Pre-flight: capture the regression baseline FIRST

The whole phase is gated on byte-identical output. Capture golden CSVs from the **current**
(pre-refactor) `backtest.py` before touching anything, on a fixed window and the CSV data
source (deterministic, no live MT5). XAU requires weekday data; BTC is 24/7.

- [ ] **Step 0a: Capture golden trade logs (current code, before any edit)**

Run (BTC + XAU, fixed 2-month window, CSV source):
```bash
for sc in baseline s3a st3 s3a_st3; do
  python backtest.py --symbols BTCUSDm XAUUSDm --months 2 --scenario $sc \
    --data-source csv --export "tests/golden/golden_${sc}.csv"
done
```
Expected: 4 CSVs written under `tests/golden/`. Commit them as the regression fixtures.

- [ ] **Step 0b: Commit the golden fixtures**

```bash
git add tests/golden/golden_baseline.csv tests/golden/golden_s3a.csv \
        tests/golden/golden_st3.csv tests/golden/golden_s3a_st3.csv
git commit -m "test(strategy): capture pre-refactor golden trade logs (regression gate)"
```

> NOTE: `data/` is gitignored but `data/history/*.csv` must exist locally for the CSV
> source. If absent, run `fetch_history.py` first (one-time, ~7yr download).

---

## File Structure

- Create `strategy/__init__.py` — re-exports the public API.
- Create `strategy/context.py` — `MarketContext` dataclass (read-only view of one symbol at one bar).
- Create `strategy/types.py` — `AnalystResult`, `Decision`, `EntryDecision`, `ExitDecision` dataclasses.
- Create `strategy/scorer.py` — `Scorer` Protocol + `MockScorer` (wraps `compute_analyst_score`).
- Create `strategy/config_strategy.py` — `ConfigStrategy` reproducing the current per-scenario `filter_cfg` logic.
- Modify `backtest.py` — build `MarketContext`, call `ConfigStrategy`, drop inlined decision branches.
- Create `tests/test_strategy_context.py`, `tests/test_strategy_mockscorer.py`,
  `tests/test_config_strategy.py`, `tests/test_backtest_regression.py`.

Boundary rule (enforced by review): nothing under `strategy/` imports `MetaTrader5`,
`requests`, `sqlite3`, or `ai_engine`. Pure functions over `MarketContext` only.

---

### Task 1: `Decision` / `AnalystResult` value types

**Files:**
- Create: `strategy/types.py`
- Test: `tests/test_strategy_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_types.py
from strategy.types import AnalystResult, EntryDecision, ExitDecision

def test_analyst_result_fields():
    r = AnalystResult(score=8.0, decision="BUY", reason="rsi<40 + H1 aligned")
    assert r.score == 8.0 and r.decision == "BUY"

def test_entry_decision_carries_trigger_and_dir():
    d = EntryDecision(direction="SELL", trigger_reason="trend_sell", conviction=1.0)
    assert d.direction == "SELL" and d.trigger_reason == "trend_sell"

def test_exit_decision_carries_ticket_reason():
    d = ExitDecision(ticket=123, reason="SL")
    assert d.ticket == 123 and d.reason == "SL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy'`

- [ ] **Step 3: Write minimal implementation**

```python
# strategy/types.py
from dataclasses import dataclass

@dataclass(frozen=True)
class AnalystResult:
    score: float
    decision: str          # "BUY" | "SELL" | "HOLD"
    reason: str = ""

@dataclass(frozen=True)
class EntryDecision:
    direction: str         # "BUY" | "SELL"
    trigger_reason: str    # e.g. "mean_reversion" | "trend_sell"
    conviction: float = 1.0

@dataclass(frozen=True)
class ExitDecision:
    ticket: int
    reason: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_types.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add strategy/types.py tests/test_strategy_types.py
git commit -m "feat(strategy): Decision + AnalystResult value types"
```

---

### Task 2: `MarketContext` — read-only per-bar view

**Files:**
- Create: `strategy/context.py`
- Test: `tests/test_strategy_context.py`

The context holds exactly what `run_backtest`'s per-bar loop currently passes around:
the per-TF trailing slices (`m5_slice`, `m15_slice`, `h1_slice`, `h4_slice`, `d1_slice`),
director state (`allowed_direction`, `h4_trend`, `d1_trend`), `bar_index`, `bar_time`,
the symbol string and its `SYMBOLS_CONFIG` entry, and the list of open positions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_context.py
import pandas as pd
from strategy.context import MarketContext

def _df(n=30):
    return pd.DataFrame({"time": range(n), "open": range(n), "high": range(n),
                         "low": range(n), "close": range(n)})

def test_context_exposes_slices_and_state():
    ctx = MarketContext(
        symbol="BTCUSDm", cfg={"lot": 0.01},
        m5=_df(), m15=_df(), h1=_df(), h4=_df(), d1=_df(),
        allowed_direction="BOTH", h4_trend="UPTREND", d1_trend="DOWNTREND",
        bar_index=100, bar_time=pd.Timestamp("2026-01-01"),
        open_positions=[],
    )
    assert ctx.symbol == "BTCUSDm"
    assert ctx.d1_trend == "DOWNTREND"
    assert len(ctx.m5) == 30
    assert ctx.open_positions == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy.context'`

- [ ] **Step 3: Write minimal implementation**

```python
# strategy/context.py
from dataclasses import dataclass, field
from typing import Any
import pandas as pd

@dataclass
class MarketContext:
    symbol: str
    cfg: dict
    m5: pd.DataFrame
    m15: pd.DataFrame
    h1: pd.DataFrame
    h4: pd.DataFrame
    d1: pd.DataFrame
    allowed_direction: str
    h4_trend: str
    d1_trend: str
    bar_index: int
    bar_time: Any
    open_positions: list = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_context.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add strategy/context.py tests/test_strategy_context.py
git commit -m "feat(strategy): MarketContext read-only per-bar view"
```

---

### Task 3: `Scorer` protocol + `MockScorer`

**Files:**
- Create: `strategy/scorer.py`
- Modify: none (wraps existing `backtest.compute_analyst_score`)
- Test: `tests/test_strategy_mockscorer.py`

`MockScorer.score(ctx, direction)` must return the SAME `(score, rsi)` the current
`compute_analyst_score(m5_slice, m15_slice, h1_slice, direction)` returns — wrapped in an
`AnalystResult`. Keep the call signature identical to avoid any numeric drift.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_mockscorer.py
import pandas as pd
from strategy.context import MarketContext
from strategy.scorer import MockScorer
import backtest

def test_mockscorer_matches_compute_analyst_score(monkeypatch):
    captured = {}
    def fake(m5, m15, h1, direction):
        captured["direction"] = direction
        return {"score": 8, "rsi": 33.0}
    monkeypatch.setattr(backtest, "compute_analyst_score", fake)

    df = pd.DataFrame({"close": range(120)})
    ctx = MarketContext("BTCUSDm", {}, df, df, df, df, df,
                        "BOTH", "UPTREND", "UPTREND", 100, 0, [])
    res = MockScorer().score(ctx, "BUY")
    assert res.score == 8 and captured["direction"] == "BUY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_mockscorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy.scorer'`

- [ ] **Step 3: Write minimal implementation**

```python
# strategy/scorer.py
from typing import Protocol
from strategy.context import MarketContext
from strategy.types import AnalystResult

class Scorer(Protocol):
    def score(self, ctx: MarketContext, direction: str) -> AnalystResult: ...

class MockScorer:
    """Backtest scorer — wraps backtest.compute_analyst_score unchanged."""
    def score(self, ctx: MarketContext, direction: str) -> AnalystResult:
        import backtest  # late import: avoid cycle at module load
        out = backtest.compute_analyst_score(ctx.m5, ctx.m15, ctx.h1, direction)
        return AnalystResult(score=out["score"], decision=direction, reason="mock")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_mockscorer.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add strategy/scorer.py tests/test_strategy_mockscorer.py
git commit -m "feat(strategy): Scorer protocol + MockScorer wrapping compute_analyst_score"
```

---

### Task 4: `ConfigStrategy.evaluate_entry` — extract the entry decision

**Files:**
- Create: `strategy/config_strategy.py`
- Reference (read, do not yet modify): `backtest.py:563-740` (the per-bar entry block:
  trend-sell path `filter_cfg["trend_sell"]`, ATR filter, RSI→direction, scenario gates).
- Test: `tests/test_config_strategy.py`

`ConfigStrategy` is constructed with the same `filter_cfg` dict `run_backtest` builds today
(`backtest.py:522-545`). `evaluate_entry(ctx, scorer)` returns an `EntryDecision | None`,
reproducing exactly:
1. trend-sell path (st1/st2/st3) gated on `d1_trend == "DOWNTREND"` + throttle/confirm;
2. else mean-reversion: RSI→direction, ATR filter, dead-hours/peak/buy-sell-only/score
   gates, `scorer.score(...)` threshold compare.

Keep the helper predicates (`donchian_breakdown`, `ema_cross_down`, `rsi_cross_down`,
`_is_safe_bar_time`, the dead-hour sets) imported from `backtest` so values are identical.

- [ ] **Step 1: Write the failing test (trend-sell branch)**

```python
# tests/test_config_strategy.py
import pandas as pd
from strategy.context import MarketContext
from strategy.config_strategy import ConfigStrategy

def _ctx(d1_trend, **kw):
    df = pd.DataFrame({"time": range(120), "open": range(120), "high": range(120),
                       "low": range(120), "close": range(120)})
    base = dict(symbol="BTCUSDm", cfg={}, m5=df, m15=df, h1=df, h4=df, d1=df,
                allowed_direction="BOTH", h4_trend="N/A", d1_trend=d1_trend,
                bar_index=100, bar_time=pd.Timestamp("2026-01-01 10:00"),
                open_positions=[])
    base.update(kw)
    return MarketContext(**base)

def test_no_trend_sell_when_d1_not_downtrend():
    cfg = {"trend_sell": True, "ts_rsi": True, "ts_donchian": False, "ts_ema": False,
           "ts_throttle_bars": 0, "ts_confirm_ema": False, "atr_filter": False,
           "atr_min": {}, "xau_dead_hours": (), "xau_peak_only": False,
           "xau_sell_only": False, "xau_buy_only": False, "btc_dead_hours": (),
           "btc_score_always": False, "xau_score_min": 0, "score_blacklist": set()}
    strat = ConfigStrategy(cfg, threshold=6)
    # d1 UPTREND → trend-sell must not fire
    d = strat.evaluate_entry(_ctx("UPTREND"), scorer=_StubScorer(score=0))
    assert d is None or d.trigger_reason != "trend_sell"

class _StubScorer:
    def __init__(self, score): self._s = score
    def score(self, ctx, direction):
        from strategy.types import AnalystResult
        return AnalystResult(score=self._s, decision=direction, reason="stub")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy.config_strategy'`

- [ ] **Step 3: Write minimal implementation**

Port the entry logic verbatim from `backtest.py` per-bar block into a method. Reuse
`backtest`'s predicate helpers so numbers are identical:

```python
# strategy/config_strategy.py
from strategy.types import EntryDecision

class ConfigStrategy:
    def __init__(self, filter_cfg: dict, threshold: int):
        self.cfg = filter_cfg
        self.threshold = threshold
        self._ts_last_bar = -10**9

    def evaluate_entry(self, ctx, scorer):
        import backtest as bt
        cfg = self.cfg
        # 1) trend-sell path — gated on confirmed D1 DOWNTREND
        if cfg["trend_sell"] and ctx.d1_trend == "DOWNTREND":
            fired = (
                (cfg["ts_donchian"] and bt.donchian_breakdown(ctx.m5)) or
                (cfg["ts_ema"]      and bt.ema_cross_down(ctx.m5)) or
                (cfg["ts_rsi"]      and bt.rsi_cross_down(ctx.m5))
            )
            if fired and self._trend_sell_allowed(ctx, bt):
                self._ts_last_bar = ctx.bar_index
                return EntryDecision(direction="SELL", trigger_reason="trend_sell")
        # 2) mean-reversion path — RSI→direction, gates, scorer threshold
        return self._mean_reversion_entry(ctx, scorer, bt)
```

> The engineer must port `_trend_sell_allowed` (throttle `ts_throttle_bars`, `ts_confirm_ema`)
> and `_mean_reversion_entry` (ATR filter, dead-hours, peak/buy-sell-only, score blacklist,
> `xau_score_min`, `btc_score_always`, threshold compare) line-for-line from
> `backtest.py:626-740`. Do not paraphrase the gate conditions — copy them and reference
> `backtest.py` line numbers in a comment for each gate.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_strategy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/config_strategy.py tests/test_config_strategy.py
git commit -m "feat(strategy): ConfigStrategy.evaluate_entry extracted from backtest"
```

---

### Task 5: `ConfigStrategy.evaluate_exit` — extract the exit/SL-TP decision

**Files:**
- Modify: `strategy/config_strategy.py`
- Reference: `backtest.py:compute_dynamic_sl_tp` (291) and the position-close booking loop
  (`backtest.py:580-605`).
- Test: `tests/test_config_strategy.py` (add cases)

Exit in the backtest is deterministic (SL/TP computed at entry via `compute_dynamic_sl_tp`,
exit bar found by forward scan). Phase 1 keeps that engine-side; `evaluate_exit` only needs
to express "is this open position closed at this bar and why" if/where the engine consults
the strategy. If the engine keeps exit bookkeeping inline (recommended for byte-identity),
`evaluate_exit` returns `None` and this task only documents the seam. Implement the minimal
pass-through + a test asserting `None`, so Phase 2/3 have the hook.

- [ ] **Step 1: Write failing test**

```python
def test_evaluate_exit_is_passthrough_in_phase1():
    from strategy.config_strategy import ConfigStrategy
    strat = ConfigStrategy({"trend_sell": False}, threshold=6)
    assert strat.evaluate_exit(ctx=None, position={"ticket": 1}) is None
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: evaluate_exit`)
- [ ] **Step 3: Implement**

```python
    def evaluate_exit(self, ctx, position):
        # Phase 1: exits stay engine-side (deterministic SL/TP forward-scan) for
        # byte-identity. Hook reserved for Phase 2/3 live exit decisions.
        return None
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(strategy): evaluate_exit pass-through hook (Phase 1)"`

---

### Task 6: Package public API

**Files:**
- Create: `strategy/__init__.py`
- Test: `tests/test_strategy_types.py` (add import-surface assertion)

- [ ] **Step 1: Failing test**

```python
def test_public_api_surface():
    from strategy import (MarketContext, AnalystResult, EntryDecision,
                          ExitDecision, Scorer, MockScorer, ConfigStrategy)
    assert all([MarketContext, AnalystResult, EntryDecision, ExitDecision,
                Scorer, MockScorer, ConfigStrategy])
```

- [ ] **Step 2: Run → FAIL** (ImportError)
- [ ] **Step 3: Implement**

```python
# strategy/__init__.py
from strategy.context import MarketContext
from strategy.types import AnalystResult, EntryDecision, ExitDecision
from strategy.scorer import Scorer, MockScorer
from strategy.config_strategy import ConfigStrategy
__all__ = ["MarketContext", "AnalystResult", "EntryDecision", "ExitDecision",
           "Scorer", "MockScorer", "ConfigStrategy"]
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(strategy): package public API"`

---

### Task 7: Route `backtest.run_backtest` through `ConfigStrategy`

**Files:**
- Modify: `backtest.py` — replace the inlined per-bar entry block (`563-740`) with: build
  `MarketContext`, call `ConfigStrategy(filter_cfg, threshold).evaluate_entry(ctx, MockScorer())`,
  act on the returned `EntryDecision`. Keep all exit/booking/risk code unchanged.
- Test: `tests/test_backtest_regression.py`

This is the integration task. The decision logic must move with ZERO numeric change. The
regression test (Task 8) is the real gate; this task wires it up.

- [ ] **Step 1: Write the (initially failing) regression smoke test**

```python
# tests/test_backtest_regression.py
import subprocess, sys, pathlib, filecmp

GOLDEN = pathlib.Path("tests/golden")

def _run(scenario, out):
    subprocess.run([sys.executable, "backtest.py", "--symbols", "BTCUSDm", "XAUUSDm",
                    "--months", "2", "--scenario", scenario, "--data-source", "csv",
                    "--export", out], check=True)

def test_s3a_byte_identical(tmp_path):
    out = tmp_path / "s3a.csv"
    _run("s3a", str(out))
    assert filecmp.cmp(out, GOLDEN / "golden_s3a.csv", shallow=False)
```

- [ ] **Step 2: Run to verify it fails (pre-integration drift OR passes if logic identical)**

Run: `pytest tests/test_backtest_regression.py::test_s3a_byte_identical -v`
Expected: FAIL only if extraction changed a value. If it already passes, good — the
extraction was clean; proceed.

- [ ] **Step 3: Implement the rewire in `backtest.py`**

Build the context once per bar (the slices are already computed at `backtest.py:627-629`):
```python
        from strategy import MarketContext, ConfigStrategy, MockScorer
        strat  = ConfigStrategy(filter_cfg, threshold)   # once per symbol, before the loop
        scorer = MockScorer()
        ...
        # inside the per-bar loop, replacing the inlined entry block:
        ctx = MarketContext(
            symbol=symbol, cfg=cfg, m5=m5_slice, m15=m15_slice, h1=h1_slice,
            h4=h4_slice if 'h4_slice' in dir() else h4_df.iloc[max(0,p_h4+1-50):p_h4+1],
            d1=d1_df.iloc[max(0,p_d1+1-50):p_d1+1],
            allowed_direction=director_state["allowed_direction"],
            h4_trend=director_state.get("h4_trend","N/A"),
            d1_trend=director_state.get("d1_trend","N/A"),
            bar_index=i, bar_time=bar_time, open_positions=open_positions)
        decision = strat.evaluate_entry(ctx, scorer)
        if decision is None:
            continue
        direction = decision.direction
        # ... existing entry execution (compute_dynamic_sl_tp, fill sim) unchanged ...
```

> CRITICAL: the strategy's internal `_ts_last_bar` throttle state replaces the loop-local
> `ts_last_bar` (`backtest.py:551`). Remove the loop-local; the strategy now owns it. Verify
> the throttle scenario `s3a_st3t` still matches golden — it exercises this state.

- [ ] **Step 4: Run the regression test to verify it passes**

Run: `pytest tests/test_backtest_regression.py -v`
Expected: PASS (byte-identical)

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_regression.py
git commit -m "refactor(backtest): drive entry decisions through ConfigStrategy (byte-identical)"
```

---

### Task 8: Full regression gate — all four scenarios byte-identical

**Files:**
- Modify: `tests/test_backtest_regression.py` (add baseline/st3/s3a_st3 cases)

- [ ] **Step 1: Add the remaining scenario cases**

```python
import pytest

@pytest.mark.parametrize("scenario", ["baseline", "s3a", "st3", "s3a_st3"])
def test_scenario_byte_identical(tmp_path, scenario):
    out = tmp_path / f"{scenario}.csv"
    _run(scenario, str(out))
    assert filecmp.cmp(out, GOLDEN / f"golden_{scenario}.csv", shallow=False), \
        f"{scenario} diverged from golden"
```

- [ ] **Step 2: Run the full gate**

Run: `pytest tests/test_backtest_regression.py -v`
Expected: PASS (4 passed). Each scenario's exported trade CSV is byte-identical to the
pre-refactor golden.

- [ ] **Step 3: If any scenario diverges**

Diff the offending CSV vs golden to find the first differing trade, trace it to the gate that
moved, and fix the port in `strategy/config_strategy.py` (a paraphrased condition is the
usual culprit). Re-run until all four pass. Do NOT regenerate the golden to match — the
golden is the source of truth.

- [ ] **Step 4: Commit**

```bash
git add tests/test_backtest_regression.py
git commit -m "test(strategy): full byte-identical regression gate (4 scenarios)"
```

---

## Self-Review checklist (run before handing off)

- [ ] Spec coverage: `MarketContext` (Task 2), `AnalystResult` (Task 1), `Scorer`+`MockScorer`
  (Task 3), `Decision` (Task 1), `Strategy` protocol surface via `ConfigStrategy` (Tasks 4–6),
  backtest engine adapter (Task 7), Phase-1 regression gate byte-identical (Tasks 0, 8). ✔
- [ ] Boundary: nothing in `strategy/` imports MT5/requests/sqlite/ai_engine (late `import
  backtest` only, which is itself import-guarded for MT5). ✔
- [ ] No live (`trade_manager`) changes — Phase 2/3 deferred. ✔
- [ ] Type consistency: `evaluate_entry → EntryDecision|None`, `evaluate_exit → ExitDecision|None`,
  `Scorer.score(ctx, direction) → AnalystResult` used identically in Tasks 3/4/7. ✔

## Execution note

Highest-risk task is Task 7 (the rewire). Treat Task 8's four-scenario byte-identity as the
hard gate: the refactor is only "done" when all four match golden with zero diff.
