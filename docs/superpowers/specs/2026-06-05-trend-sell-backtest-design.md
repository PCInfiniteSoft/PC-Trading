# Trend-Following SELL Backtest Comparison — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorm) — pending implementation plan
**Component:** `backtest.py` scenario system (research only — no live-code changes)

## Problem

On 2026-06-05 the live bot took zero trades all day in a steady downtrend:

- **BTC** never produced a signal — RSI sat in the 42–43 dead zone, below the SELL
  trigger (RSI > ~57) and above the BUY trigger (RSI < ~37).
- **XAU** produced only counter-trend BUY signals (RSI oversold 22–26), all correctly
  blocked by GUARDIAN-L (`BUY — D1 DOWNTREND`) and GUARDIAN-F (Dead Hour).

The live entry model is **mean-reversion**: SELL fires only when `rsi > strat['sell'][i]`
(sell the overbought bounce). In a one-directional grind down with no bounce, the SELL
side never triggers, so the bot sits out trending-down days entirely.

This project explores adding a **trend-following SELL** entry path and decides which
trigger to pursue **by backtest comparison**, before any live change.

## Goal

Compare three trend-following SELL triggers against the current baseline, over BTC + XAU
for 6 months, using a composite metric set, to pick a winner. Producing the winner's live
implementation is explicitly a **separate, later spec** — out of scope here.

## Design

### 1. Architecture

Extend the existing scenario system in `backtest.py` (a 760-line harness with
`--scenario` filtering, mock DIRECTOR / SCOUT / GUARDIAN / ANALYST, and metric output).
Add three scenarios: `st1`, `st2`, `st3`. Each enables a **second SELL entry path that
runs in parallel with** the existing mean-reversion SELL — the baseline behavior is
preserved; the new trigger only *adds* entries.

The new SELL path fires only when a **D1/H4 DOWNTREND is confirmed** (reuse the existing
`compute_director` / `_get_trend` helpers), so it never shorts against an uptrend. All
existing mock GUARDIAN gates still apply to the added entries.

### 2. Trigger definitions

Computed from the M5/H1 data the harness already loads. Parameters are starting values,
tunable later.

| Scenario | Added SELL condition (on top of baseline) |
|----------|-------------------------------------------|
| `st1` Donchian breakdown | `close < min(low over prior 20 bars)` AND D1 DOWN |
| `st2` EMA cross | `EMA9 < EMA21` AND `price < EMA21` AND D1 DOWN |
| `st3` RSI momentum cross | RSI crosses down through 50 (`prev >= 50 and now < 50`) AND D1 DOWN |

### 3. Comparison metrics (composite)

Run each scenario in `{baseline, st1, st2, st3}` × `{BTCUSDm, XAUUSDm}` and emit a
comparison table with, per scenario/symbol:

- Total PnL
- Win rate %
- Max drawdown %
- Sharpe
- Number of trades
- Number of SELL trades
- **Number of SELL-trades-in-downtrend** (the gap this work targets — must rise vs baseline)
- Average R multiple

The SELL-in-downtrend count is the primary evidence that the gap is closed; the rest guard
against the trigger doing so unprofitably.

### 4. Scope

In scope:
- Restore `backtest.py`, `run_scenarios.py`, `test_backtest.py` from `main` (currently
  deleted in the working tree).
- Add the three scenarios + their trigger logic to `backtest.py`.
- Run all scenarios and write the comparison table to a results file.

Out of scope:
- Any change to live trading code (`trade_manager.py`, `ai_engine.py`, `bot_config.py`,
  `risk_manager.py`).
- Porting the winning trigger to live — that is a separate spec gated on these results.

### 5. Caveat (backtest fidelity)

The harness uses a **mock ANALYST**, not the live GPT model. Relative comparison between
triggers is trustworthy; absolute numbers will not match live. Conclusions must be framed
as "which trigger is relatively better," not "expected live PnL."

### 6. Decision gate

After the run, review the comparison table, pick the winning trigger (or conclude none
beats baseline), and open a **new separate spec** to port the winner into the live entry
path. No live change is made as part of this work.

## Testing

- Extend `test_backtest.py` with unit tests for the three new trigger predicates
  (Donchian breakdown, EMA-cross, RSI-cross-down), using small synthetic OHLC slices —
  asserting each fires/does-not-fire on hand-constructed bars, and that all three are
  gated off when D1 is not DOWN.
