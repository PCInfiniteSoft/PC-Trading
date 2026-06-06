# Trend-Following SELL Backtest — Results

**Run date:** 2026-06-06 (BTC+XAU, 6 months, risk L3, lot 0.03, spread 0.2 pips)
**Harness:** `backtest.py` (mock ANALYST — relative comparison only, not live PnL)
**Note:** st1/st2/st3 rows are baseline mean-reversion + the trend-sell trigger combined
(the trend-sell path fires only on D1 DOWNTREND bars; other bars fall through to baseline).

## Composite table

| scenario | symbol  | trades | WR%  | PnL     | MaxDD% | Sharpe | #SELL | #SELLdn | avgPnL |
|----------|---------|-------:|-----:|--------:|-------:|-------:|------:|--------:|-------:|
| baseline | BTCUSDm | 1785   | 34.0 | 561.18  | 69.2   | 2.92   | 825   | 373     | 0.314  |
| baseline | XAUUSDm | 397    | 33.2 | 90.48   | 100.0  | 0.46   | 189   | 111     | 0.228  |
| **st1** Donchian | BTCUSDm | 1917 | 35.7 | **1879.10** | 69.2 | 2.38 | 1071 | 595 | 0.98 |
| **st1** Donchian | XAUUSDm | 622  | 35.0 | **2019.96** | 100.0 | 2.78 | 363 | 220 | 3.248 |
| **st2** EMA | BTCUSDm | 1654 | 33.7 | 488.32 | 82.7 | 1.09 | 934 | 479 | 0.295 |
| **st2** EMA | XAUUSDm | 396  | 36.4 | 1055.10 | 100.0 | 2.88 | 230 | 104 | 2.664 |
| **st3** RSI-cross | BTCUSDm | 2022 | 35.3 | 1015.84 | **67.1** | **3.38** | 1145 | **717** | 0.502 |
| **st3** RSI-cross | XAUUSDm | 664  | 32.7 | 1037.69 | 100.0 | 1.71 | 441 | 304 | 1.563 |

## Reading

- **st1 (Donchian breakdown) — max raw PnL.** Huge PnL lift both symbols (BTC +561→+1879,
  XAU +90→+2020), WR up, BTC DD unchanged. But BTC **Sharpe dropped** (2.92→2.38) — bigger
  but lumpier returns. XAU avgPnL 3.25 is the standout.
- **st3 (RSI cross-down 50) — best risk-adjusted.** Only trigger that **beats baseline on
  Sharpe (BTC 3.38 vs 2.92) AND drawdown (67.1% vs 69.2%) AND PnL (+1016)** simultaneously,
  and it adds the **most SELL-in-downtrend coverage** (#SELLdn 717, ~2× baseline). XAU also
  solidly up (PnL +1038, Sharpe 1.71).
- **st2 (EMA alignment) — reject.** The state-trigger fires on many bars yet BTC PnL barely
  moves (+488, below baseline) with **worse DD (82.7%)** and collapsed Sharpe (1.09). Dilutive.

### Caveat on XAU MaxDD
XAU MaxDD reads 100% across **all** scenarios incl. baseline — the harness equity model
floors for XAU, so the DD column is non-discriminating for XAU. Judge XAU on PnL / Sharpe /
WR, not DD.

## Verdict

**Winner (composite, risk-adjusted): st3 — RSI cross-down through 50.**
It is the only trigger that improves every risk dimension (Sharpe + DD + PnL) on BTC while
maximizing downtrend SELL coverage — directly closing the "no-trade in grinding downtrend"
gap that motivated this work.

**Aggressive alternative: st1 — Donchian breakdown** if maximizing raw PnL outweighs the
Sharpe/DD trade-off.

**st2 (EMA) dropped.**

## Next step

Open a **separate live-port spec** to bring **st3** (and optionally A/B it with **st1**)
into the live entry path in `trade_manager.py` — with the live GUARDIAN-L (counter-trend),
Gate Q (spacing) and the real GPT ANALYST applied. Re-validate live before trusting absolute
numbers (this run is mock-AI relative).
