# S3-Balanced (Approach A) — Design Spec

**Date:** 2026-05-14
**Branch:** main
**Capital assumption:** $300, lot 0.03, spread 0.20 pips, 3-month backtest window

---

## Background

S3 baseline is the only profitable scenario (+$537) out of 5 tested, but carries a 26.5%
max drawdown — too high for live deployment. Analysis of the 827 S3 trades identified four
specific loss clusters that account for most of the drawdown without contributing meaningful
PnL.

### Loss clusters identified

| Cluster | Trades | WR | PnL |
|---|---|---|---|
| BTC hour 0:00–1:59 | 179 | ~31% | -$123 |
| XAU hour 0:00 | 11 | 0% | -$101 |
| XAU hour 9:00 | 7 | 0% | -$47 |
| XAU SELL direction | 72 | 30.6% | -$107 |
| Score = 8 band | 19 | 15.8% | -$103 |

Eliminating these clusters while keeping the profitable core lifts PnL from $537 → $851
and cuts max drawdown from 26.5% → 10.8%.

---

## Goal

Produce a new backtest scenario `s3a` that encodes four filter rules derived from the
analysis above, verify it reproduces the simulated metrics, then port the rules into
production guards.

---

## Filter Rules

### Rule 1 — BTC Dead-Hour Block
- **Scope:** BTCUSDm only
- **Block:** UTC hour 0 and 1 (00:00–01:59)
- **Reason:** Asian session dead zone; 179 trades in these hours produce -$123 PnL at
  sub-32% WR with no compensating upside.

### Rule 2 — XAU Dead-Hour Block
- **Scope:** XAUUSDm only
- **Block:** UTC hour 0 and hour 9 (00:00–00:59, 09:00–09:59)
- **Reason:** Both hours recorded 0% WR across the full 3-month window (-$148 combined).

### Rule 3 — XAU Direction Lock
- **Scope:** XAUUSDm only
- **Allowed:** BUY only; SELL entries rejected
- **Reason:** XAU BUY = +$520 (47 trades, 36.2% WR). XAU SELL = -$107 (72 trades, 30.6%
  WR). SELL is a consistent drain across every hour and score band sampled.

### Rule 4 — Score Anomaly Gate
- **Scope:** All symbols
- **Block:** entries where analyst score == 8
- **Reason:** Score-8 band has 15.8% WR — lowest of all bands and well below the 34%
  baseline. Bands 5, 6, 7, 9, 10 are all retained.

---

## Expected Metrics (from simulation)

| Metric | S3 baseline | S3-A (Approach A) |
|---|---|---|
| Trades | 827 | 536 |
| Win Rate | 34.7% | 37.1% |
| Total PnL | $537 | $851 |
| Max Drawdown | 26.5% | 10.8% |
| PnL / trade | $0.65 | $1.59 |
| PnL / %DD ratio | 20.3 | 78.8 |

---

## Implementation Scope

### `backtest.py`
Add scenario `s3a` to the `choices` list and extend `filter_cfg` inside `run_backtest`:

```python
"btc_dead_hours":  (0, 1) if sc == "s3a" else (),
"xau_dead_hours":  (0, 9) if sc in ("s1", "s3a") else (2, 5, 6, 17, 22),
"xau_buy_only":    sc in ("s3a",),
"score_blacklist": {8} if sc == "s3a" else set(),
```

The existing `xau_peak_only` flag from S3 is retained unchanged in `s3a`.

### `run_scenarios.py`
Add `"s3a"` to the `SCENARIOS` list so it runs in the batch.

### Production code (post-backtest validation)
If `s3a` backtest metrics match simulation within ±5%:
- Add dead-hour guards in `risk_manager.py` (Gate e, after existing 4 gates)
- Add XAU direction lock in `risk_manager.py` (Gate f)
- Add score blacklist check in `risk_manager.py` (Gate g)
- Document new gates in `spec.md` under the GUARDIAN section

**No changes to:** `ai_engine.py`, `shared_state.py`, database schema, Discord/Flask layer.

---

## Validation Plan

1. Run `python run_scenarios.py` with `s3a` in the SCENARIOS list.
2. Confirm output CSV trade count ≈ 536, PnL ≈ $851, MaxDD ≈ 10.8%.
3. Spot-check: verify no XAU SELL entries exist in output CSV.
4. Spot-check: verify no BTC entries at hour 0 or 1 exist in output CSV.
5. Spot-check: verify no entries with score = 8 exist in output CSV.
6. If all checks pass, port rules to production `risk_manager.py`.

---

## Out of Scope

- Changing the analyst scoring formula (score=8 is gated, not fixed)
- Adding new indicators or signal sources
- Modifying SL/TP logic (Chandelier exits remain unchanged)
- Changing lot sizing or risk level defaults
