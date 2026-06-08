# SP-B — Dynamic Lot (Risk-Per-Trade) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed per-symbol `lot` with a risk-per-trade sizing layer so every trade risks the same fraction of equity (self-deleveraging on drawdown), shipped **inert behind a default-OFF flag**.

**Architecture:** A pure sizing function `size_by_risk(...)` in `risk_manager.py` computes lot from `equity × risk_pct ÷ money-at-SL`, clamped to broker min/step/max and a config `lot_max` sanity cap. It takes the `conviction_mult` argument (default `1.0`) — the pinned **A→B interface** that SP-C will later supply. `place_order` calls it only when `risk_sizing_enabled` is True on the symbol profile; otherwise the existing fixed-lot path is byte-for-byte unchanged. The existing `lot_mult` seam (reduced-size trend-sell rollout) is preserved and composes multiplicatively on top.

**Tech Stack:** Python 3.12, MetaTrader5 (`mt5.symbol_info`, `mt5.account_info`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-07-strategy-separation-design.md` §SP-B. A→B interface: `size(signal, equity, profile, conviction_mult=1.0) -> lot`.

---

## Context for the implementer (read before Task 1)

Current sizing in `trade_manager.py`:

- `place_order(symbol, type, price, rsi, comment, extra=None)` (L785) sets `lot = SYMBOLS_CONFIG[symbol]["lot"]` (fixed), then optionally scales by `extra["lot_mult"]` via `scaled_lot()` (L775, pure clamp helper), then caps at config `lot_max`.
- The stop distance is **a percent of price, not the `sl_pts` config field**: `sl_pct = 0.001 * (1 + (3 - risk_level) * 0.2)`; `sl_dist = price * sl_pct` (L806–810). The sizing layer MUST use this same `sl_dist` so "risk_pct of equity" is the true loss when SL hits.
- Money lost if SL hit (MT5): `lot × (sl_dist_price / tick_size) × tick_value`, where `tick_size = symbol_info.trade_tick_size`, `tick_value = symbol_info.trade_tick_value`.

Inverting for lot:

```
lot = (equity × risk_pct × conviction_mult) / ((sl_dist_price / tick_size) × tick_value)
```

`risk_manager.py` is a `RiskManager` class of pre-trade gates plus module-level helpers/constants at the top. Add the sizing function as a **module-level pure function** there (no MT5/IO inside it — caller passes the numbers), mirroring how `scaled_lot` is a pure helper.

Why a flag: SP-B changes lot sizing on a live ~$146 account. Like the SP-A `xau_trend_sell_enabled` toggle, SP-B ships **inert** (`risk_sizing_enabled` default False) and is enabled deliberately after the sizing is sanity-checked live.

---

## File structure

- `bot_config.py` — add `risk_pct` and `risk_sizing_enabled` to each symbol's `strategy` profile. (Modify)
- `strategy_profile.py` — add `risk_pct(symbol)` and `risk_sizing_enabled(symbol)` accessors. (Modify)
- `risk_manager.py` — add pure `size_by_risk(...)` function + a `_clamp_lot(...)` helper. (Modify)
- `trade_manager.py` — `place_order` calls `size_by_risk` when the flag is on; reorder so `sl_dist` is computed before lot. (Modify)
- `test_risk_sizing.py` — new unit tests for the sizing math, clamps, and the `conviction_mult=1.0` identity. (Create)
- `CLAUDE.md` — add an SP-B rollout section. (Modify)

---

## Task 1: Profile fields — `risk_pct` + `risk_sizing_enabled`

**Files:**
- Modify: `bot_config.py` (both `BTCUSDm` and `XAUUSDm` `strategy` dicts)
- Modify: `strategy_profile.py`
- Test: `test_strategy_profile.py`

- [ ] **Step 1: Write the failing test**

Add to `test_strategy_profile.py`:

```python
import strategy_profile as sp

def test_risk_pct_default_and_per_symbol():
    # BTC and XAU each declare a risk_pct; unknown symbol falls back to 0.0 (sizing off)
    assert sp.risk_pct("BTCUSDm") == 0.005
    assert sp.risk_pct("XAUUSDm") == 0.005
    assert sp.risk_pct("UNKNOWN") == 0.0

def test_risk_sizing_disabled_by_default():
    assert sp.risk_sizing_enabled("BTCUSDm") is False
    assert sp.risk_sizing_enabled("XAUUSDm") is False
    assert sp.risk_sizing_enabled("UNKNOWN") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_strategy_profile.py::test_risk_pct_default_and_per_symbol test_strategy_profile.py::test_risk_sizing_disabled_by_default -v`
Expected: FAIL with `AttributeError: module 'strategy_profile' has no attribute 'risk_pct'`

- [ ] **Step 3: Add the profile fields in `bot_config.py`**

In the `BTCUSDm` `"strategy"` dict, add after `"xau_trend_sell_enabled": False,`:

```python
            # ── SP-B risk-per-trade sizing ──
            "risk_pct": 0.005,            # risk 0.5% of equity per trade if SL hits
            "risk_sizing_enabled": False, # inert until deliberately enabled (live ~$146)
```

Make the identical addition in the `XAUUSDm` `"strategy"` dict.

- [ ] **Step 4: Add accessors in `strategy_profile.py`**

After `trend_sell_cfg`:

```python
def risk_pct(symbol: str) -> float:
    """Fraction of equity risked per trade (SP-B). 0.0 = no per-symbol value → sizing off."""
    return float(get_profile(symbol).get("risk_pct", 0.0))


def risk_sizing_enabled(symbol: str) -> bool:
    """SP-B master flag. False → place_order keeps the fixed-lot path unchanged."""
    return bool(get_profile(symbol).get("risk_sizing_enabled", False))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest test_strategy_profile.py -v`
Expected: PASS (existing + 2 new)

- [ ] **Step 6: Commit**

```bash
git add bot_config.py strategy_profile.py test_strategy_profile.py
git commit -m "feat(strategy): add SP-B risk_pct + risk_sizing_enabled profile fields (inert)"
```

---

## Task 2: Pure sizing function `size_by_risk` + clamp helper

**Files:**
- Modify: `risk_manager.py`
- Test: `test_risk_sizing.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test_risk_sizing.py`:

```python
import math
import pytest
from risk_manager import size_by_risk, _clamp_lot


# _clamp_lot(lot, volume_min, volume_step, volume_max, lot_max)
def test_clamp_rounds_to_step_and_floors_to_min():
    assert _clamp_lot(0.034, 0.01, 0.01, 100.0, None) == 0.03
    assert _clamp_lot(0.004, 0.01, 0.01, 100.0, None) == 0.01   # below min → min

def test_clamp_respects_volume_max_and_lot_max():
    assert _clamp_lot(5.0, 0.01, 0.01, 2.0, None) == 2.0        # broker max
    assert _clamp_lot(5.0, 0.01, 0.01, 100.0, 0.05) == 0.05     # config sanity cap

# size_by_risk(equity, risk_pct, sl_dist_price, tick_size, tick_value,
#              volume_min, volume_step, volume_max, lot_max, conviction_mult=1.0)
def test_risk_sizing_basic_math():
    # equity 1000, risk 0.5% = $5 at risk. sl_dist 10 price units.
    # tick_size 0.01, tick_value 1.0 → ticks_at_sl = 1000, money/lot = 1000.
    # lot = 5 / 1000 = 0.005 → clamps up to min 0.01
    lot = size_by_risk(1000, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None)
    assert lot == 0.01

def test_risk_sizing_scales_with_equity():
    big = size_by_risk(100000, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None)
    small = size_by_risk(10000, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None)
    assert big > small   # self-deleveraging: less equity → smaller lot

def test_conviction_mult_identity_and_scaling():
    base = size_by_risk(100000, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None,
                        conviction_mult=1.0)
    half = size_by_risk(100000, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None,
                        conviction_mult=0.5)
    assert half == pytest.approx(base / 2, abs=0.01)

def test_invalid_inputs_return_volume_min():
    # zero/negative sl_dist, tick_value, or equity must not divide-by-zero or oversize
    assert size_by_risk(1000, 0.005, 0.0, 0.01, 1.0, 0.01, 0.01, 100.0, None) == 0.01
    assert size_by_risk(0, 0.005, 10.0, 0.01, 1.0, 0.01, 0.01, 100.0, None) == 0.01
    assert size_by_risk(1000, 0.005, 10.0, 0.01, 0.0, 0.01, 0.01, 100.0, None) == 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_risk_sizing.py -v`
Expected: FAIL with `ImportError: cannot import name 'size_by_risk' from 'risk_manager'`

- [ ] **Step 3: Implement in `risk_manager.py`**

Add module-level (after the constants near the top, before `class RiskManager`):

```python
def _clamp_lot(lot, volume_min, volume_step, volume_max, lot_max):
    """Round to broker step, then clamp to [volume_min, min(volume_max, lot_max)]."""
    step = volume_step if volume_step and volume_step > 0 else 0.01
    lot = round(lot / step) * step
    upper = volume_max
    if lot_max is not None:
        upper = min(upper, lot_max)
    lot = min(lot, upper)
    lot = max(lot, volume_min)
    return round(lot, 2)


def size_by_risk(equity, risk_pct, sl_dist_price, tick_size, tick_value,
                 volume_min, volume_step, volume_max, lot_max, conviction_mult=1.0):
    """Risk-per-trade lot. Pure: caller supplies equity, the stop distance actually
    used, and the symbol's tick geometry. Returns a clamped lot.

    money_at_sl_per_lot = (sl_dist_price / tick_size) * tick_value
    lot = (equity * risk_pct * conviction_mult) / money_at_sl_per_lot

    Any non-positive input (equity, sl_dist, tick_size, tick_value) yields volume_min
    so a bad number can never produce a divide-by-zero or an oversized position.

    NOTE: pass `symbol_info.trade_tick_value` (the loss-side tick value). MT5 also exposes
    `trade_tick_value_loss`; on a USD account for BTC/XAU they are equal, so the plain
    `trade_tick_value` is correct here.
    """
    if (equity <= 0 or risk_pct <= 0 or sl_dist_price <= 0
            or tick_size <= 0 or tick_value <= 0 or conviction_mult <= 0):
        return volume_min
    money_at_sl_per_lot = (sl_dist_price / tick_size) * tick_value
    if money_at_sl_per_lot <= 0:
        return volume_min
    raw = (equity * risk_pct * conviction_mult) / money_at_sl_per_lot
    return _clamp_lot(raw, volume_min, volume_step, volume_max, lot_max)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest test_risk_sizing.py -v`
Expected: PASS (all 6)

- [ ] **Step 5: Commit**

```bash
git add risk_manager.py test_risk_sizing.py
git commit -m "feat(risk): pure risk-per-trade size_by_risk + lot clamp (SP-B A->B hook)"
```

---

## Task 3: Wire `size_by_risk` into `place_order` behind the flag

**Files:**
- Modify: `trade_manager.py` (`place_order`, L785–823)
- Test: `test_risk_sizing.py` (add an integration-style test with a fake symbol_info)

**Design:** Compute `sl_dist` BEFORE lot. When `sp.risk_sizing_enabled(symbol)` is True and `mt5.account_info()` + `mt5.symbol_info()` are available, set `lot = size_by_risk(...)` using `conviction_mult = extra.get("conviction_mult", 1.0)` (A→B interface). Then apply the existing `lot_mult` seam and `lot_max` cap unchanged (they compose on top). When the flag is False or any MT5 read is None, keep `lot = SYMBOLS_CONFIG[symbol]["lot"]` exactly as today.

- [ ] **Step 1: Write the failing test**

Add to `test_risk_sizing.py` a test of a small extracted helper `resolve_lot` (Step 3 extracts it so the branch is unit-testable without MT5):

```python
def test_resolve_lot_flag_off_returns_base():
    from trade_manager import resolve_lot
    # flag off → base lot regardless of equity
    lot = resolve_lot(symbol="BTCUSDm", enabled=False, base_lot=0.01,
                      equity=100000, sl_dist=10.0, tick_size=0.01, tick_value=1.0,
                      volume_min=0.01, volume_step=0.01, volume_max=100.0,
                      lot_max=0.05, conviction_mult=1.0, risk_pct=0.005)
    assert lot == 0.01

def test_resolve_lot_flag_on_uses_risk_size():
    from trade_manager import resolve_lot
    lot = resolve_lot(symbol="BTCUSDm", enabled=True, base_lot=0.01,
                      equity=100000, sl_dist=10.0, tick_size=0.01, tick_value=1.0,
                      volume_min=0.01, volume_step=0.01, volume_max=100.0,
                      lot_max=0.05, conviction_mult=1.0, risk_pct=0.005)
    # equity 100000 * 0.005 = 500 at risk; money/lot = (10/0.01)*1 = 1000 → 0.5 lot,
    # capped by lot_max 0.05
    assert lot == 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_risk_sizing.py::test_resolve_lot_flag_off_returns_base test_risk_sizing.py::test_resolve_lot_flag_on_uses_risk_size -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_lot' from 'trade_manager'`

- [ ] **Step 3: Extract `resolve_lot` and rewire `place_order`**

In `trade_manager.py`, add a pure helper above `place_order`:

```python
def resolve_lot(symbol, enabled, base_lot, equity, sl_dist, tick_size, tick_value,
                volume_min, volume_step, volume_max, lot_max, conviction_mult, risk_pct):
    """Choose the lot for an order. Flag OFF → base_lot (legacy path, unchanged).
    Flag ON → risk-per-trade size. Clamps live inside size_by_risk."""
    if not enabled:
        return base_lot
    return size_by_risk(equity, risk_pct, sl_dist, tick_size, tick_value,
                        volume_min, volume_step, volume_max, lot_max,
                        conviction_mult=conviction_mult)
```

`size_by_risk` is a **module-level function**, not a method of `RiskManager`. The current import (L16) is `from risk_manager import RiskManager`. Change it to:

```python
from risk_manager import RiskManager, size_by_risk
```

so `resolve_lot` (and the test) can call `size_by_risk` directly.

Then rewrite the top of `place_order` (replace L786–810 up to the `sl_dist`/`tp_dist` computation) so `sl_dist` is computed first and feeds sizing:

```python
def place_order(symbol, type, price, rsi, comment, extra=None):
    ex = extra or {}
    base_lot = SYMBOLS_CONFIG[symbol]["lot"]

    # Stop distance (percent-of-price, risk-level adjusted) — computed first so the
    # risk-per-trade sizer can size against the stop that will actually be placed.
    risk_level = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    base_pct = 0.001
    sl_pct = base_pct * (1.0 + (3 - risk_level) * 0.2)
    tp_pct = sl_pct * 1.0
    sl_dist = price * sl_pct
    tp_dist = price * tp_pct

    # ── SP-B risk-per-trade sizing (inert unless profile flag is on) ──
    si = mt5.symbol_info(symbol)
    acct = mt5.account_info()
    if sp.risk_sizing_enabled(symbol) and si is not None and acct is not None:
        lot = resolve_lot(
            symbol=symbol, enabled=True, base_lot=base_lot,
            equity=acct.equity, sl_dist=sl_dist,
            tick_size=si.trade_tick_size, tick_value=si.trade_tick_value,
            volume_min=si.volume_min, volume_step=si.volume_step or 0.01,
            volume_max=si.volume_max, lot_max=SYMBOLS_CONFIG[symbol].get("lot_max"),
            conviction_mult=ex.get("conviction_mult", 1.0),
            risk_pct=sp.risk_pct(symbol),
        )
    else:
        lot = base_lot  # legacy fixed-lot path, byte-for-byte unchanged

    # Optional reduced-size multiplier (trend-sell rollout) — composes on top.
    _mult = ex.get("lot_mult", 1.0)
    if _mult != 1.0:
        if si is not None:
            lot = scaled_lot(lot, _mult, si.volume_min, si.volume_step or 0.01)
        else:
            logging.getLogger(symbol).warning(f"⚠️ symbol_info None during lot_mult={_mult} — using lot {lot}")

    # Hard safety ceiling (config lot_max).
    _lot_max = SYMBOLS_CONFIG[symbol].get("lot_max")
    if _lot_max is not None:
        lot = min(lot, _lot_max)
```

Then continue with the existing `raw_comment`/`safe_comment` and the rest of `place_order`. **Delete** the now-duplicated `risk_level`/`sl_pct`/`tp_pct`/`sl_dist`/`tp_dist` block that previously sat lower down (L805–811), since it has moved up. Keep the `if type == "BUY": ... sl_price/tp_price` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest test_risk_sizing.py -v`
Expected: PASS (8 total)

- [ ] **Step 5: Full regression — confirm legacy path unchanged**

Run: `.venv/Scripts/python.exe -m pytest test_backtest.py test_layer_spacing.py test_strategy_profile.py test_trend_sell_wiring.py test_news.py test_risk_sizing.py -q`
Expected: PASS (all). The fixed-lot path is the default (flag OFF) so existing behavior is preserved.

- [ ] **Step 6: Commit**

```bash
git add trade_manager.py test_risk_sizing.py
git commit -m "feat(strategy): wire risk-per-trade sizing into place_order behind flag (inert)"
```

---

## Task 4: SP-B rollout documentation

**Files:**
- Modify: `CLAUDE.md` (add a `## Strategy Rollout — SP-B Risk Sizing` section)

- [ ] **Step 1: Add the rollout section to `CLAUDE.md`**

Append after the existing XAU trend-sell rollout section:

```markdown
## Strategy Rollout — SP-B Risk-Per-Trade Sizing

Risk-per-trade lot sizing is wired into `place_order` but **disabled by default**
(`risk_sizing_enabled: False` on each symbol profile in `bot_config.py`). Fully inert
until the toggle is on — the fixed-lot path runs unchanged.

**To enable:**
1. Confirm `risk_pct` per symbol (default 0.005 = 0.5% equity at risk per trade).
2. Set `SYMBOLS_CONFIG["<SYM>"]["strategy"]["risk_sizing_enabled"] = True`.
3. Deploy via the normal procedure.

**Pre-enable checks (verify the day the toggle goes on):**
- **Sanity vs current lot:** at live equity, log the computed lot for one signal before
  trusting it. With ~$146 equity and `risk_pct 0.005`, the risk-per-trade lot is *expected*
  to floor at broker `volume_min` (0.01) for both symbols (near-parity at first, scaling up
  as equity grows) — but this rests on the broker's `trade_tick_value`/`trade_tick_size`,
  which are not confirmed offline. **Confirm via the logged-lot gate before enabling**, do
  not assume. Raise `risk_pct` only after live evidence.
- **`lot_max` ceiling is the backstop:** each symbol's `lot_max` (XAU 0.05) hard-caps the
  result, so a small `sl_dist` or equity spike cannot oversize.
- **`sl_dist` source:** sizing uses the percent-of-price stop actually placed
  (`price * sl_pct`), not `sl_pts`. If the SL formula changes, re-check sizing.
- **Composes with `lot_mult`:** the trend-sell reduced-size seam multiplies on top of the
  risk-sized lot; half-size trend-sell rollout still halves the final lot.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: SP-B risk-sizing rollout switch + pre-enable checklist"
```

---

## Self-review notes (spec coverage)

- **risk-per-trade formula** → Task 2 (`size_by_risk`).
- **`risk_pct` per symbol on profile** → Task 1.
- **self-deleveraging** → Task 2 `test_risk_sizing_scales_with_equity`.
- **`conviction_mult` default 1.0 (A→B interface)** → Task 2 signature + identity test; Task 3 reads `extra["conviction_mult"]`. No other consumer (SP-C plugs in later).
- **clamps (broker min/max + sanity cap)** → Task 2 `_clamp_lot`, exercised by Task 2 clamp tests + Task 3 `lot_max` cap.
- **inert until deliberately enabled** → Task 1 flag default False + Task 3 branch + Task 4 doc.
- **identity: sizing with default == pure risk-per-trade** → conviction_mult default 1.0 is the identity; no separate parity needed since the flag (not conviction) gates legacy vs new.

**Open items deferred to enable-time tuning (per spec §Open items):** concrete `risk_pct` is seeded at 0.005 and tuned against backtest then live; `lot_max` already set per symbol from SP-A.

**Dependency note:** plan-B depends only on the pinned A→B interface (`conviction_mult=1.0`) and is independent of plan-C. It does not touch the GUARDIAN chain. Unrelated to the GUARDIAN-M churn bug (separate backlog).
