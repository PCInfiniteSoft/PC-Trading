# Dynamic Slippage Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GUARDIAN-M measure true broker slippage (re-fetch tick before send) and gate it with a volatility/spread-aware dynamic threshold instead of a static `max_slip`.

**Architecture:** A pure function `compute_dyn_slip()` in `risk_manager.py` (where GUARDIAN gate math already lives, e.g. Gate Q) computes the threshold in points from price/ATR/spread + per-symbol config. `place_order` in `trade_manager.py` re-fetches a fresh tick immediately before `order_send`, uses it for the request price + slippage baseline, then calls `compute_dyn_slip()` and logs a full breakdown on every fill. Config keys move into `SYMBOLS_CONFIG`.

**Tech Stack:** Python 3.12, MetaTrader5, plain `assert`-style unit tests run directly (matching `test_layer_spacing.py`), pytest-compatible.

**Verified facts (server, 2026-06-05):** `BTCUSDm` point=0.01 digits=2 spread~1008pts; `XAUUSDm` point=0.001 digits=3 spread~280pts. `STRATEGY_DATA[symbol]["atr_pct"]` exists (percent of price, set in `ai_engine.py:335`). `place_order` signature: `place_order(symbol, type, price, rsi, comment, extra=None)` at `trade_manager.py:749`. Current GUARDIAN-M static check at `trade_manager.py:825-830`. Tick fetched `:429`/`:512`, used `:460`/`:543`.

---

## File Structure

- **`risk_manager.py`** (modify) — add module-level pure function `compute_dyn_slip(price, point, atr_pct, ask, bid, cfg)`. No new imports needed (pure arithmetic). Lives beside existing gate logic.
- **`test_slippage_guard.py`** (create) — unit tests for `compute_dyn_slip`, mirroring the structure of `test_layer_spacing.py` (no MT5 calls; values injected).
- **`bot_config.py`** (modify) — in `SYMBOLS_CONFIG`, replace `max_slip` with `slip_base` / `slip_a_atr` / `slip_b_spread` / `slip_cap` for both `BTCUSDm` and `XAUUSDm`.
- **`trade_manager.py`** (modify) — in `place_order` (`:749-833`): (1) re-fetch fresh tick before building the request and use it as `price`; (2) replace the static `max_slip` block with a `compute_dyn_slip` call + breakdown logging.

---

## Task 1: Pure threshold function `compute_dyn_slip` (risk_manager.py)

**Files:**
- Modify: `risk_manager.py` (add module-level function, after imports / near other gate helpers)
- Test: `test_slippage_guard.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test_slippage_guard.py`:

```python
"""
Unit tests for GUARDIAN-M dynamic slippage threshold (compute_dyn_slip).
No MT5 terminal: price/point/atr/spread/cfg are injected directly.
"""
from risk_manager import compute_dyn_slip

# Provisional per-symbol config (mirrors bot_config defaults)
BTC = {"slip_base": 600, "slip_a_atr": 0.02, "slip_b_spread": 1.0, "slip_cap": 1800}
XAU = {"slip_base": 300, "slip_a_atr": 0.02, "slip_b_spread": 1.0, "slip_cap": 900}


def test_base_only_when_atr_and_spread_missing():
    # atr_pct None and ask==bid (zero spread) -> only base contributes.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=100000.0, bid=100000.0, cfg=BTC)
    assert dyn == 600
    assert bd["atr"] == 0.0 and bd["spread"] == 0.0


def test_high_atr_widens_threshold():
    # BTC atr_pct=0.3% -> atr_pts = 0.003*100000/0.01 = 30000; term = 0.02*30000 = 600.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=0.3,
                               ask=100000.0, bid=100000.0, cfg=BTC)
    assert bd["atr"] == 600.0
    assert dyn == 1200  # 600 base + 600 atr, under cap 1800


def test_wide_spread_widens_threshold():
    # spread = (ask-bid)/point = (100010-100000)/0.01 = 1000 pts; term = 1.0*1000 = 1000.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=100010.0, bid=100000.0, cfg=BTC)
    assert bd["spread"] == 1000.0
    assert dyn == 1600  # 600 + 1000, under cap


def test_clamped_at_cap():
    # Huge ATR -> raw far over cap -> clamped to slip_cap.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=5.0,
                               ask=100010.0, bid=100000.0, cfg=BTC)
    assert dyn == 1800
    assert bd["raw"] > 1800


def test_never_tighter_than_base_non_regression():
    # Any non-negative terms -> dyn >= base (the non-regression invariant).
    dyn, _ = compute_dyn_slip(price=3300.0, point=0.001, atr_pct=0.0,
                              ask=3300.0, bid=3300.0, cfg=XAU)
    assert dyn >= 300


def test_xau_config_selected():
    # XAU base 300 distinct from BTC base 600.
    dyn, bd = compute_dyn_slip(price=3300.0, point=0.001, atr_pct=None,
                               ask=3300.0, bid=3300.0, cfg=XAU)
    assert bd["base"] == 300 and dyn == 300


def test_negative_or_zero_spread_ignored():
    # bid > ask (crossed/garbage) must not produce a negative spread term.
    dyn, bd = compute_dyn_slip(price=100000.0, point=0.01, atr_pct=None,
                               ask=99990.0, bid=100000.0, cfg=BTC)
    assert bd["spread"] == 0.0
    assert dyn == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_slippage_guard.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_dyn_slip' from 'risk_manager'`

- [ ] **Step 3: Implement `compute_dyn_slip` in `risk_manager.py`**

Add at module level (top-level `def`, not a method), after the imports:

```python
def compute_dyn_slip(price, point, atr_pct, ask, bid, cfg):
    """GUARDIAN-M dynamic slippage ceiling, in points.

    dyn = base + a*atr_pts + b*spread_pts, clamped to [.., slip_cap].
    Falls back to slip_base alone when atr_pct/spread are unavailable, so the
    result is never tighter than slip_base (non-regression invariant).

    Returns (dyn_slip_pts: float, breakdown: dict) — breakdown is for logging.
    """
    base = cfg.get("slip_base", 300)
    a = cfg.get("slip_a_atr", 0.0)
    b = cfg.get("slip_b_spread", 0.0)
    cap = cfg.get("slip_cap", base)

    atr_term = 0.0
    if atr_pct and atr_pct > 0 and price > 0 and point > 0:
        atr_pts = (atr_pct / 100.0) * price / point
        atr_term = a * atr_pts

    spread_term = 0.0
    if ask and bid and point > 0:
        spread_pts = (ask - bid) / point
        if spread_pts > 0:
            spread_term = b * spread_pts

    raw = base + atr_term + spread_term
    dyn = min(raw, cap)
    breakdown = {
        "base": base,
        "atr": round(atr_term, 1),
        "spread": round(spread_term, 1),
        "raw": round(raw, 1),
        "cap": cap,
        "dyn": round(dyn, 1),
    }
    return dyn, breakdown
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_slippage_guard.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add risk_manager.py test_slippage_guard.py
git commit -m "feat(guardian-m): pure compute_dyn_slip threshold + unit tests"
```

---

## Task 2: Per-symbol config keys (bot_config.py)

**Files:**
- Modify: `bot_config.py` — `SYMBOLS_CONFIG["BTCUSDm"]` (`max_slip` at `:77`) and `SYMBOLS_CONFIG["XAUUSDm"]` (`max_slip` at `:107`)

- [ ] **Step 1: Replace BTC `max_slip`**

In `bot_config.py`, BTC block, replace:

```python
        # Max slippage (pts) — GUARDIAN-M ปิดออเดอร์ทันทีถ้า fill ห่างจาก price เกินนี้
        "max_slip": 600,
```

with:

```python
        # GUARDIAN-M dynamic slippage ceiling: dyn = base + a*ATR_pts + b*spread_pts, capped.
        # base = previous static max_slip (non-regression). ATR coeff is small because
        # ATR-in-points is large at point=0.01 (~3e4). spread ~1x current spread (~1008pts).
        "slip_base": 600,
        "slip_a_atr": 0.02,
        "slip_b_spread": 1.0,
        "slip_cap": 1800,
```

- [ ] **Step 2: Replace XAU `max_slip`**

In `bot_config.py`, XAU block, replace:

```python
        # Max slippage (pts) — GUARDIAN-M ปิดออเดอร์ทันทีถ้า fill ห่างจาก price เกินนี้
        "max_slip": 300,
```

with:

```python
        # GUARDIAN-M dynamic slippage ceiling (see BTC block). point=0.001, spread ~280pts.
        "slip_base": 300,
        "slip_a_atr": 0.02,
        "slip_b_spread": 1.0,
        "slip_cap": 900,
```

- [ ] **Step 3: Verify config imports cleanly**

Run: `python -c "from bot_config import SYMBOLS_CONFIG; print(SYMBOLS_CONFIG['BTCUSDm']['slip_base'], SYMBOLS_CONFIG['XAUUSDm']['slip_cap'])"`
Expected: `600 900`

- [ ] **Step 4: Confirm no stale `max_slip` references remain**

Run: `grep -rn "max_slip" *.py`
Expected: no matches (the only consumer was `trade_manager.py:826`, rewired in Task 3). If any remain, they must be updated in Task 3.

- [ ] **Step 5: Commit**

```bash
git add bot_config.py
git commit -m "config: replace static max_slip with dynamic slip_* keys (BTC/XAU)"
```

---

## Task 3: Wire into `place_order` — re-fetch tick + dynamic check + logging (trade_manager.py)

**Files:**
- Modify: `trade_manager.py` — `place_order` body (`:749-833`)

- [ ] **Step 1: Re-fetch fresh tick before building the request**

In `place_order`, the request currently uses the stale `price` argument. Immediately before the `sl_dist`/`tp_dist` computation (currently `trade_manager.py:760`), insert a fresh-tick override. Replace:

```python
    risk_level = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    base_pct = 0.001 
    sl_pct = base_pct * (1.0 + (3 - risk_level) * 0.2) 
    tp_pct = sl_pct * 1.0

    sl_dist = price * sl_pct
```

with:

```python
    risk_level = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    base_pct = 0.001 
    sl_pct = base_pct * (1.0 + (3 - risk_level) * 0.2) 
    tp_pct = sl_pct * 1.0

    # [GUARDIAN-M] re-fetch tick right before send so deviation + slippage are measured
    # against the live market, not the stale tick captured before the ANALYST AI call.
    fresh = mt5.symbol_info_tick(symbol)
    if fresh is None:
        logging.getLogger(symbol).error("❌ [GUARDIAN-M] no fresh tick — abort order")
        return False
    price = fresh.ask if type == "BUY" else fresh.bid

    sl_dist = price * sl_pct
```

- [ ] **Step 2: Replace the static slippage block with the dynamic check + logging**

Current block at `trade_manager.py:825-830`:

```python
    sym_cfg = SYMBOLS_CONFIG.get(symbol, {})
    max_slip = sym_cfg.get('max_slip', 300)
    if slippage > max_slip:
        logging.getLogger(symbol).warning(f"🛑 [GUARDIAN-M] Slip {slippage:.0f}pts > {max_slip} — ปิดออเดอร์ทันที")
        close_one_order(symbol=symbol, reason=f"GUARDIAN-M: slip {slippage:.0f}pts", ticket=res.order)
        return False
```

Replace with (note `compute_dyn_slip` import at top of step):

```python
    sym_cfg = SYMBOLS_CONFIG.get(symbol, {})
    point = mt5.symbol_info(symbol).point
    atr_pct = ai.STRATEGY_DATA.get(symbol, {}).get("atr_pct")
    dyn_slip, bd = compute_dyn_slip(price, point, atr_pct, fresh.ask, fresh.bid, sym_cfg)
    logging.getLogger(symbol).info(
        f"[GUARDIAN-M] slip={slippage:.0f} dyn_slip={bd['dyn']:.0f} "
        f"[base={bd['base']} atr={bd['atr']} spread={bd['spread']} "
        f"raw={bd['raw']} cap={bd['cap']}]")
    if slippage > dyn_slip:
        logging.getLogger(symbol).warning(
            f"🛑 [GUARDIAN-M] Slip {slippage:.0f}pts > {dyn_slip:.0f} — ปิดออเดอร์ทันที")
        close_one_order(symbol=symbol, reason=f"GUARDIAN-M: slip {slippage:.0f}pts", ticket=res.order)
        return False
```

- [ ] **Step 3: Add the `compute_dyn_slip` import**

At the top of `trade_manager.py`, the existing import is `from risk_manager import RiskManager` (`:24`). Replace it with:

```python
from risk_manager import RiskManager, compute_dyn_slip
```

- [ ] **Step 4: Syntax + import smoke check**

Run: `python -c "import ast; ast.parse(open('trade_manager.py', encoding='utf-8').read()); print('parse OK')"`
Expected: `parse OK`

Run: `python -c "from risk_manager import compute_dyn_slip; print('import OK')"`
Expected: `import OK`

(Full `import trade_manager` may require the `discord` package; the AST parse above is the authoritative syntax check for this environment. Live import is verified on the server during rollout.)

- [ ] **Step 5: Re-run the unit tests (still green)**

Run: `python -m pytest test_slippage_guard.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Commit**

```bash
git add trade_manager.py
git commit -m "feat(guardian-m): re-fetch tick + dynamic slippage check with breakdown log"
```

---

## Task 4: Deploy + live verify

**Files:** none (deploy procedure per `CLAUDE.md`)

- [ ] **Step 1: Push branch and merge to main** (after user approval)

```bash
git checkout main && git merge --no-ff slippage-dynamic-guard
git push origin main
```

- [ ] **Step 2: Pull on server + drop deploy.flag**

```bash
ssh -i "C:/Users/PC-Laptop/.ssh/id_ed25519" Administrator@100.106.19.75 "cd C:\Users\Administrator\Desktop\PC-Trading && git pull origin main"
ssh -i "C:/Users/PC-Laptop/.ssh/id_ed25519" Administrator@100.106.19.75 "echo. > C:\Users\Administrator\Desktop\PC-Trading\deploy.flag"
```

- [ ] **Step 3: Confirm restart + GUARDIAN-M logs appear**

Inspect the latest log under `C:\Users\Administrator\Desktop\PC-Trading\Logs\` for `[GUARDIAN-M] slip=... dyn_slip=...` lines on the next fills.
Expected: each fill logs a breakdown; rejects fire only when `slip > dyn_slip`.

- [ ] **Step 4: Tune note**

After 1–2 days, query the breakdown logs and adjust `slip_a_atr` / `slip_b_spread` / `slip_cap` from the observed `slip` vs `dyn_slip` distribution. Also confirm the `deviation=20` question: if re-fetch keeps real slip small, GUARDIAN-M becomes a rarely-firing backstop (expected, fine).

---

## Self-Review

- **Spec coverage:** §1 re-fetch → Task 3 Step 1. §2 dynamic formula → Task 1. §3 config → Task 2. §4 logging → Task 3 Step 2. §5 testing → Task 1 (pure-function tests, TDD). §6 verify/rollout → Task 4. All covered.
- **Placeholder scan:** no TBD/TODO; every code step shows full code; commands have expected output.
- **Type/name consistency:** `compute_dyn_slip(price, point, atr_pct, ask, bid, cfg)` and its `(dyn, breakdown)` return + breakdown keys (`base/atr/spread/raw/cap/dyn`) are identical across Task 1 def, Task 1 tests, and Task 3 call site. Config keys (`slip_base/slip_a_atr/slip_b_spread/slip_cap`) identical across Task 1 tests, Task 2 config, Task 3 consumer.
- **Non-regression:** Task 2 sets `slip_base` = prior static `max_slip`; terms ≥ 0 and `slip_cap > slip_base`, so `dyn_slip ≥ slip_base` always → never tighter than today.
