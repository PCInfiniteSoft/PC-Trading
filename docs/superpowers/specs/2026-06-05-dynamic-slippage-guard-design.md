# Dynamic Slippage Guard — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorm) — pending implementation plan
**Component:** GUARDIAN-M (slippage guard) in `trade_manager.place_order`

## Problem

GUARDIAN-M closes a freshly-opened order when measured slippage exceeds a static
per-symbol `max_slip` (BTC 600 pts, XAU 300 pts in `bot_config.py`).

On 2026-06-04, 6 of 12 closed trades were GUARDIAN-M slip rejects with slippage of
694 / 483 (XAU) and 1573 / 1111 / 903 / 4831 / 2752 (BTC) pts. Each reject still costs
spread + commission (observed -$0.30 to -$0.84 per reject).

### Root cause

`tick` is fetched at `trade_manager.py:429` (BUY) / `:512` (SELL), then an **ANALYST
AI call** runs before `place_order` is invoked at `:460` / `:543`. `place_order`
receives the now-stale `tick.ask`/`tick.bid` as `price` and uses it both as the order
request price and as the baseline for `slippage = abs(res.price - price)`.

Consequently the measured "slippage" is dominated by **market drift during AI latency**,
not by true broker fill slippage. The 4831 pt figure on a fast-moving BTC is consistent
with this. (`deviation=20` in the request is, empirically, not bounding fills to 20 pts —
to be verified during rollout; brokers may ignore `deviation` on IOC market deals.)

## Goals

1. Measure **true broker slippage** (fix the stale-price root cause).
2. Replace the static `max_slip` with a **dynamic threshold** that scales with current
   volatility (ATR) and spread, so legitimate fills in fast/wide markets are not rejected
   while genuine bad fills still are.

## Design

### 1. Root cause fix — re-fetch tick before `order_send`

In `place_order`, immediately before building the request, fetch a fresh tick and use it
as the execution price:

```python
fresh = mt5.symbol_info_tick(symbol)
if fresh is None:
    return False
price = fresh.ask if type == "BUY" else fresh.bid   # override the stale caller tick
```

- `request["price"]`, `sl_price`, `tp_price` are all computed from this fresh `price`,
  so `deviation=20` is evaluated against the live market.
- `slippage = abs(res.price - price)` now reflects broker fill slippage, not AI latency.
- Callers at `:460` / `:543` still pass `tick.ask`/`tick.bid`; `place_order` only uses
  that for context/logging, no longer for pricing.
- Note: entry RSI / decision logic still uses the older tick — that is a separate concern
  (signal freshness) and is out of scope here.

### 2. Dynamic threshold

```python
atr_pts    = (atr_pct / 100) * price / point        # atr_pct from STRATEGY_DATA[symbol]
spread_pts = (fresh.ask - fresh.bid) / point
dyn_slip   = slip_base + slip_a_atr * atr_pts + slip_b_spread * spread_pts
dyn_slip   = min(dyn_slip, slip_cap)                # hard ceiling
# trigger GUARDIAN-M when: slippage > dyn_slip
```

- **Fallback:** if `atr_pct` or the spread is unavailable/non-positive, fall back to
  `slip_base` alone (conservative — never wider than base when data is missing).
- Extracted as a **pure function** `compute_dyn_slip(price, point, atr_pct, ask, bid, cfg)`
  returning `(dyn_slip, breakdown_dict)` so it is unit-testable without MT5.

### 3. Config — `bot_config.py` SYMBOLS_CONFIG

Replace `max_slip` with four keys per symbol. Starting values (conservative — close to
current behavior, tuned later from live logs):

| Symbol | slip_base | slip_a_atr | slip_b_spread | slip_cap |
|--------|-----------|------------|---------------|----------|
| BTCUSDm | 150 | 0.5 | 1.5 | 800 |
| XAUUSDm | 80 | 0.5 | 1.5 | 400 |

### 4. Logging / observability

On every fill (both pass and reject) log the decision and its breakdown:

```
GUARDIAN-M slip=<X> dyn_slip=<Y> [base=<b> atr=<a_term> spread=<s_term> cap=<cap>]
```

This produces the data needed to tune the coefficients from real fills.

### 5. Testing (TDD)

Unit tests for `compute_dyn_slip()` (no MT5 dependency):

- base-only fallback when `atr_pct` is `None` / spread non-positive
- high ATR widens threshold
- wide spread widens threshold
- result clamped at `slip_cap`
- per-symbol config selection (BTC vs XAU)

### 6. Verify / rollout

1. Deploy via standard procedure (commit → push → server `git pull` → `deploy.flag`).
2. Watch live `GUARDIAN-M slip=... dyn_slip=...` logs for 1–2 days.
3. Tune `slip_*` coefficients from observed `slip` vs `dyn_slip` distribution.
4. **Verify the `deviation=20` question:** confirm whether the re-fetch alone keeps slip
   within `deviation`, in which case GUARDIAN-M may rarely trigger and serves as a
   defense-in-depth backstop.

## Out of scope

- Signal/decision freshness (RSI computed off the older tick).
- Changing `deviation` or `type_filling` (revisit only if rollout shows it is needed).
