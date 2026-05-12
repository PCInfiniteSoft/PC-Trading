# Backtest Feature Design
**Date:** 2026-05-12  
**Approach:** A — Bar-by-bar simulation (standalone CLI script)  
**Scope:** BTCUSDm, XAUUSDm — 3 months default, configurable

---

## 1. Architecture

Single standalone file `backtest.py` in the project root. Does not modify any production code.

```
backtest.py
 ├── DataLoader        — fetch M5/H1/H4/D1 candles from MT5
 ├── MockDirector      — compute H4/D1 trend + allowed_direction via real Supertrend
 ├── MockAnalyst       — compute score 0-12 per M5 bar (Supertrend + SMC + RSI + SCOUT)
 ├── MockGuardian      — enforce 4 production gates (cooldown, direction, spread, layers)
 ├── PositionSimulator — open/close virtual positions, detect SL/TP hit on future candles
 └── ReportPrinter     — print console summary + optional CSV export
```

**CLI interface:**
```bash
python backtest.py --months 3 --risk 3
python backtest.py --months 1 --symbols BTCUSDm --export results.csv
```

**Arguments:**

| Arg | Default | Description |
|-----|---------|-------------|
| `--months` | 3 | Lookback period in months |
| `--symbols` | BTCUSDm XAUUSDm | Symbols to test |
| `--risk` | 3 | Risk level 1–5 (sets entry score threshold) |
| `--export` | (none) | Path to export CSV trade log |

Requires MT5 to be running. Uses existing credentials from `settings.txt` (same as production).

---

## 2. Data

All data fetched from MT5 at script start, stored in memory as pandas DataFrames indexed by symbol and timeframe.

| Timeframe | Candles (3 months) | Purpose |
|-----------|--------------------|---------|
| M5 | ~26,000 | Signal generation + SL/TP simulation |
| H1 | ~2,160 | SCOUT EMA alignment |
| H4 | ~540 | DIRECTOR trend |
| D1 | ~90 | DIRECTOR trend |

---

## 3. Mock Logic

### MockDirector
- Runs every 48 M5 bars (= 4 hours)
- Calls `advanced_indicators.get_macro_trends()` on a H4/D1 slice ending at the current simulated time
- Maps trends to `allowed_direction`:
  - H4 + D1 both UPTREND → `BUY_ONLY`
  - H4 + D1 both DOWNTREND → `SELL_ONLY`
  - Mixed or SIDEWAY → `BOTH`
- `NONE` (news-based pause) is not simulated in this version — future upgrade

### MockAnalyst
- Runs on every M5 bar
- Reuses `advanced_indicators.get_3_indicators()` and `get_scout_score()` on a lookback slice
- Score components (same as production):
  - Supertrend M5 aligned with direction: +3
  - RSI at threshold for layer 1: +3
  - SMC Order Block proximity: +2 to +3
  - SCOUT MACD + EMA alignment: +0 to +2
- Decision: `score >= threshold` → BUY/SELL, else HOLD
- Threshold per risk level matches `SYMBOLS_CONFIG.analyst_score_offset`

### MockGuardian
Applies 4 gates in sequence — a trade is only opened if all pass:
1. **Cooldown:** no trade within 1 M5 bar (5 min) after a SL hit on same symbol
2. **Direction:** trade direction must match `allowed_direction` from MockDirector
3. **Spread:** skip if simulated spread exceeds limit (BTCUSDm: 30 pts, XAUUSDm: 10 pts — fixed constants, not dynamic)
4. **Max layers:** no more than 3 open positions per symbol simultaneously

### PositionSimulator
- On entry signal: record `{symbol, direction, entry_bar_idx, entry_price, sl_price, tp_price, lot}`
- SL/TP values taken from `SYMBOLS_CONFIG` (same as production)
- On each subsequent M5 bar: check if `high >= tp_price` (for BUY) or `low <= sl_price` first
  - If TP hit first → WIN, `net_profit = tp_pts * pip_value * lot`
  - If SL hit first → LOSS, `net_profit = -sl_pts * pip_value * lot`
  - If both hit in same candle → SL wins (conservative assumption)
- Tracks `max_floating_profit` and `max_floating_loss` while position is open
- Pip value: read from `mt5.symbol_info(symbol).trade_tick_value` at script start — not hardcoded

---

## 4. Output

### Console report
```
============================================================
  PC TRADING — BACKTEST REPORT
  Period : 2026-02-12 → 2026-05-12  (3 months)
  Risk   : Level 3  |  Symbols: BTCUSDm, XAUUSDm
============================================================

[ BTCUSDm ]
  Total Trades      : 142
  Win Rate          : 63.4%
  Net P&L           : +$284.50
  Avg Win / Loss    : +$4.20 / -$2.80
  Max Drawdown      : 8.2%
  Max Consec. Loss  : 4

[ XAUUSDm ]
  Total Trades      : 125
  Win Rate          : 59.2%
  Net P&L           : +$147.60
  Avg Win / Loss    : +$3.90 / -$3.10
  Max Drawdown      : 6.5%
  Max Consec. Loss  : 5

[ OVERALL ]
  Total Trades      : 267
  Net P&L           : +$432.10
  Max Drawdown      : 11.4%
  Sharpe Ratio      : 1.42  (annualized, daily returns)
  Win Rate          : 61.8%
============================================================
```

### CSV export (when `--export` is specified)
Columns: `ticket, symbol, direction, entry_time, entry_price, exit_price, net_profit, exit_reason, rsi_entry, score, macro_bias, allowed_direction, layers`

---

## 5. Known Limitations (Approach A)

- No news-based NONE pauses (MockDirector never sets `allowed_direction = NONE`)
- Spread is a fixed constant, not tick-level dynamic spread
- No trailing SL or break-even simulation (positions exit only at fixed SL/TP)
- No slippage modeled
- DIRECTOR refreshes exactly every 48 bars — production refreshes on ATR spike too

These are acceptable trade-offs for Approach A and can be addressed when upgrading to Approach C.

---

## 6. Files Changed

| File | Change |
|------|--------|
| `backtest.py` | New file — entire backtest script |

No production files are modified.
