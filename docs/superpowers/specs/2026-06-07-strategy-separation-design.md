# Strategy Separation Program (SP-A / SP-B / SP-C) — Design

**Date:** 2026-06-07
**Status:** Design — approved, pending plan split
**Motivates from:** `docs/superpowers/results/2026-06-05-trend-sell-backtest-results.md`
  ("Next step" → A→B→C)

## Problem

Two coupled problems in the live bot:

1. **Tangled per-symbol logic.** BTC/XAU strategy decisions are hardcoded as inline
   `if symbol == ...` branches, `xau_buy_only`, dead-hour sets, and `score_blacklist`
   scattered through `trade_manager.py`. Hard to read, hard to change one symbol without
   risking the other.
2. **Symbols need divergent strategies.** Backtest (`s3a_st3`) showed the live `s3a`
   strategy takes **zero XAU SELL** (`xau_buy_only` blocks all XAU shorts) → blind to XAU
   downtrends. Adding the st3 trend-sell path unlocked XAU shorts: XAU PnL +295→+1100
   (+273%), Sharpe 1.94→4.16. The clean drawdown lever identified was **position sizing**,
   not entry filtering.

This program separates per-symbol strategy logic (SP-A), adds risk-per-trade dynamic lot to
control drawdown (SP-B), and calibrates a conviction table to scale bets by setup quality
(SP-C).

## Scope

One design spec covering all three sub-projects at **interface/architecture altitude**.
It will be split into **three independent implementation plans** (plan-A, plan-B, plan-C).
The two cross-cutting contracts are pinned here so the plans stay independent:

- **A→B interface:** the sizing layer accepts a `conviction_mult` argument, default `1.0`.
- **B→C interface:** SP-C populates a `bucket → conviction_mult` table with a fixed bucket-key
  schema and a bounded multiplier range (defined in §SP-C).

These backtest numbers are **mock-AI relative** (see results addendum). Absolute PnL must be
re-validated live before being trusted.

## Architecture overview

Per-symbol behavior lives in a **strategy profile** on `SYMBOLS_CONFIG[symbol]["strategy"]`.
A shared engine in `trade_manager.py` reads the profile to choose entry paths and parameters.
The GUARDIAN guard chain stays **shared and unchanged** — divergence lives in entry-path
selection and params, not in parallel class hierarchies. Lot size is computed by a new sizing
layer (SP-B). The conviction multiplier (SP-C) plugs into the sizing layer through the A→B
hook.

```
SYMBOLS_CONFIG[symbol]["strategy"] = {
    "entry_paths": ["mean_reversion", "trend_sell"],   # BTC: ["mean_reversion"] initially
    "trend_sell":  {"trigger": "rsi", "rsi_level": 50}, # = st3
    "guards": {                                         # toggles moved out of inline code
        "xau_buy_only": False,
        "dead_hours":   (7, 16),
        "score_blacklist": {8},
        ...
    },
    "risk_pct": 0.5,                                    # SP-B, per symbol
}
```

Engine loop (conceptual):

```
for path in profile["entry_paths"]:
    signal = path.evaluate(macro, scout, candles, profile)
    if signal and passes_shared_guards(signal, profile):
        lot = size(signal, equity, profile, conviction_mult)   # SP-B
        place_order(signal, lot)
```

The path model mirrors the existing backtest: the `trend_sell` path fires only on D1
DOWNTREND bars; other bars fall through to `mean_reversion`. The fired-bar claim
(`dc0e8537`) must be preserved so a blocked/invalid trend-sell signal does not leak into
mean-reversion.

## SP-A — Strategy separation (parity-gated, two steps)

SP-A has two sub-goals that must be sequenced and gated, because one is a behavior-preserving
refactor and the other is a deliberate behavior change. Mixing them makes a refactor bug
indistinguishable from an st3 effect.

### A1 — Refactor to strategy profile (behavior unchanged)

Move inline per-symbol logic into `profile`:

- `if symbol == "XAU"` / `if symbol == "BTC"` branches → profile lookups
- `xau_buy_only`, `xau_peak_only` → `profile["guards"]`
- `btc_dead_hours` / dead-hour sets → `profile["guards"]["dead_hours"]`
- `score_blacklist` → `profile["guards"]["score_blacklist"]`
- per-symbol params already in `SYMBOLS_CONFIG` stay where they are; only the **logic**
  toggles move

**Parity gate (blocking).** Capture the current `trade_manager` entry decisions over a fixed
bar set (the 6-month BTC+XAU backtest window already used). Refactor until the new
profile-driven path **reproduces those decisions exactly** (same trades, same direction,
same bar). A1 is not done until the characterization test passes. A2 does not start until A1
passes.

### A2 — Port st3 trend-sell to XAU (behavior change)

Add the `trend_sell` path (RSI cross-down through 50 = st3) to the XAU profile. This unlocks
XAU shorts in the `trend_sell` context (effectively relaxing `xau_buy_only` for that path
only — mean-reversion XAU SELL stays blocked unless separately enabled). Behavior changes →
governed by the guarded rollout (§st3 live rollout).

## SP-B — Dynamic lot (risk-per-trade)

Replace fixed `lot` with a risk-per-trade sizing layer (extend the existing
`risk_manager.py`):

```
lot = (equity * risk_pct) / (sl_distance_pts * point_value) * conviction_mult
```

- `risk_pct` is per symbol, on the profile. Each trade risks the same fraction of equity:
  if SL is hit, the loss is `risk_pct` of equity.
- **Self-deleveraging:** equity falls → lot falls → drawdown decelerates. This is the
  drawdown lever from the results addendum.
- `conviction_mult` defaults to `1.0` (the **A→B interface**); SP-C supplies real values
  later. With the default, sizing is pure risk-per-trade.
- **Clamps:** result is bounded by broker min/max lot and a sanity cap, so a small
  `sl_distance` or an equity spike cannot produce an oversized position.

### A→B interface (pinned)

```
size(signal, equity, profile, conviction_mult=1.0) -> lot
```

The sizing layer is the only consumer of `conviction_mult`. No other code reads it. SP-B
ships and controls drawdown on its own with the default; SP-C is an opt-in enhancement on
top.

## SP-C — Conviction table (replay + live blend)

Scale bets by setup quality. Buckets are keyed on **setup features**, all price-derivable so
the bucket *keys* are not mock-AI contaminated:

- `trend_alignment` (e.g. D1/H4/H1 agreement state)
- `rsi_zone` (banded)
- `session` (Asia / London / NY)
- `regime` (trend / range)

Calibration: replay `s3a_st3` over ~7 years of price history → synthetic trades →
realized expectancy per bucket → **reweight with available live trades** from
`trading_history.db`. Output is a `bucket → conviction_mult` table consumed through the
B→C interface.

### Validation gate (blocking)

Although the bucket *keys* are price-derived, the per-bucket *expectancy* is the average PnL
of trades the **mock ANALYST chose to take**. If mock entry timing differs from live, that
expectancy is biased — and SP-B's `conviction_mult` would amplify the bias straight into
position size. Therefore:

> **All `conviction_mult` values stay at `1.0` until the per-bucket expectancy is confirmed
> against live trades.** A bucket's multiplier may leave `1.0` only after its replay
> expectancy is validated against the live-trade subset for that bucket.

Until validation passes for a bucket, SP-C is a no-op for that bucket and the system behaves
as pure SP-B.

### B→C interface (pinned)

- **Bucket key schema:** `(trend_alignment, rsi_zone, session, regime)` — exact enums fixed
  in plan-C.
- **Multiplier range:** `conviction_mult ∈ [mult_min, mult_max]`, bounded (e.g. `[0.5, 2.0]`)
  so no single bucket can dominate sizing; defaults to `1.0` when unvalidated or bucket
  missing.

## st3 live rollout (guarded — real money, ~$158 equity)

Porting st3 (A2) makes the bot start taking XAU shorts in production, where it currently
takes none (`#SELLdn = 0`). This is a deliberate behavior change on a live account and must
not be a flip.

- **Toggle:** `xau_trend_sell_enabled`, default **OFF**. Enabled manually only after the A1
  parity gate passes.
- **Reduced size first:** start XAU trend-sell at half `risk_pct`; raise only after live
  evidence.
- **Monitor:** `#SELLdn`, `winner_MFE`, and realized PnL on XAU SELL for the first window
  before scaling.
- **Guards unchanged:** the shared GUARDIAN chain still applies to every `trend_sell`
  signal.

## Testing

- **A1:** characterization/parity test over the fixed 6-month bar set — the SP-A gate.
- **SP-B:** unit tests for sizing math — `risk_pct → lot`, clamp behavior (min/max/sanity),
  and `conviction_mult = 1.0` identity (sizing with the default equals pure risk-per-trade).
- **SP-C:** bucket calibration test (replay → expectancy → table) and a validation-gate
  assertion (any unvalidated bucket yields `conviction_mult = 1.0`).

## Decomposition into plans

1. **plan-A** — A1 refactor (parity gate) + A2 st3 port + guarded rollout toggle.
2. **plan-B** — risk-per-trade sizing layer with the `conviction_mult=1.0` hook.
3. **plan-C** — conviction table calibration (replay + live blend) with the validation gate.

plan-B depends only on the A→B interface; plan-C depends only on the B→C interface. With the
two contracts pinned above, the three plans are independent.

## Open items

- Exact enum sets for the SP-C bucket keys (resolved in plan-C).
- `risk_pct`, `mult_min`, `mult_max` concrete values (resolved per plan, tuned against
  backtest then live).
- 7-year price history availability for SP-C (XAU `s3a_st3q` rerun is still pending from the
  prior session — same MT5 history dependency).
