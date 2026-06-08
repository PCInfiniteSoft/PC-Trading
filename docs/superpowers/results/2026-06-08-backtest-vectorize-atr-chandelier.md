# Backtest Vectorization — ATR/Chandelier Precompute (Results)

**Date:** 2026-06-08
**Branch:** `backtest-vectorize` (commit `6cc28efd`)
**Scope:** Precompute `_calc_atr_chandelier` (atr/long_stop/short_stop) as full columns once
per TF frame; idempotent short-circuit when columns already present. RSI/EMA/MACD left
per-bar (they use `ewm(adjust=False)` over a fixed trailing window — full-column ewm seeds
differently and would NOT byte-match, so intentionally untouched).

## Correctness gate — PASSED (byte-identical)

Same scenario/params, old (per-bar recompute) vs new (precomputed), CSV data source:

| Window | Symbol | Scenario | Trades | Net P&L | Result |
|--------|--------|----------|--------|---------|--------|
| 2 months | BTCUSDm | s3a | 496 | +235.71 | sha256 **identical** (`0b52d624…`) |
| 1 month  | BTCUSDm | s3a | 260 | +88.29  | `diff` empty — **byte-identical** |

## Speedup gate — PASSED (2.66×)

Head-to-head, same machine, 1-month BTC s3a (8928 M5 bars), back-to-back:

| Variant | Wall-clock |
|---------|-----------|
| OLD (per-bar `_calc_atr_chandelier`) | 362.1 s |
| NEW (precomputed + short-circuit)    | 136.4 s |
| **Speedup** | **2.66×** |

Byte-identical AND measurably faster ⇒ the short-circuit fires (not pure overhead).

## Caveats

- Partial optimization: **ATR/Chandelier only**. RSI/EMA/MACD still recompute per bar
  (ewm-seed mismatch blocks a byte-identical full-column form). A further win would need a
  windowed-rolling-ewm reimplementation — riskier, deferred.
- Parallel-scenarios (#2 from the perf note) still not done.
- 7-year runs: still bounded by the remaining per-bar ewm work; expect a partial improvement.

## End state

Merged to `main` as a dev-tooling improvement. Does NOT touch the live bot (`backtest.py`
is not imported by `trade_manager`); no deploy required.
