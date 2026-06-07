# PC Trading 2.17 — Claude Instructions

## Deploy Procedure

When deploying code changes to the server:

1. **Commit locally** — `git commit`
2. **Push to origin** — `git push origin main`
3. **SSH to server and git pull** — `ssh Administrator@100.106.19.75 "cd C:\\Users\\Administrator\\Desktop\\PC-Trading && git pull origin main"`
4. **Drop deploy.flag** — `ssh Administrator@100.106.19.75 "echo. > C:\\Users\\Administrator\\Desktop\\PC-Trading\\deploy.flag"`

**DO NOT SCP files directly to server.** Use git pull only. SCP causes line ending issues and bypasses version control.

The bot detects `deploy.flag` at the start of each 1-min loop tick, removes it, and calls `os.execv()` to restart in-place with the new code.

## Server

- SSH: `Administrator@100.106.19.75`
- Bot path: `C:\Users\Administrator\Desktop\PC-Trading\`
- Logs: `C:\Users\Administrator\Desktop\PC-Trading\Logs\`
- DB: `C:\Users\Administrator\Desktop\PC-Trading\trading_history.db`

## Key Files

- `trade_manager.py` — main trading loop, trailing SL, place_order, fast_tick_job
- `gui_main.py` — Discord bot on_ready, task starts
- `ai_engine.py` — DIRECTOR / ANALYST / SCOUT prompts
- `bot_config.py` — SYMBOLS_CONFIG (lot, sl, max_slip, etc.)
- `shared_state.py` — global state, ACTIVE_TRADE_TRACKER

## Notion

Project page: https://www.notion.so/PC-Trading-35bd978527ea83bcbf30016b57b4286d

## Strategy Rollout — XAU Trend-Sell (st3)

The XAU trend-following SELL path (st3: RSI cross-down through 50, gated on D1 DOWNTREND) is
wired into trade_manager.py but **disabled by default**. It is fully inert until the toggle is on.

**To enable live XAU shorts:**
1. Set `SYMBOLS_CONFIG["XAUUSDm"]["strategy"]["xau_trend_sell_enabled"] = True` in `bot_config.py`.
2. Deploy via the normal deploy procedure (commit → push → server git pull → deploy.flag).
3. Monitor before scaling: `#SELLdn` (SELL-in-downtrend count), `winner_MFE`, and realized XAU SELL PnL.

**Pre-enable checklist (verify the day the toggle goes on, not before):**
- **GUARDIAN-G (SELL_ONLY):** the trend-sell branch sits after GUARDIAN-G, which blocks XAU SELL
  unless `allowed_direction == SELL_ONLY`. If the DIRECTOR rarely sets SELL_ONLY, most trend-sell
  signals will be blocked. Check live DIRECTOR output first — this is the most likely throttle.
- **Dead-hours E/F:** trend-sell inherits XAU dead-hour guards (the backtest path deliberately
  skipped them). Live is intentionally more conservative — confirm this is acceptable.
- **GUARDIAN-P pyramid:** trend-sell uses `break` (abandons the bar) when a lower layer is losing,
  vs the AI path's `continue`. More conservative by design.
- **Reduced-lot is a no-op at base lot 0.01:** `trend_sell.lot_mult = 0.5` cannot go below the
  broker minimum (0.01), so it does nothing until the base lot is raised above the minimum. To get
  genuine reduced exposure on rollout, raise the base lot first (a `lot_max = 0.05` ceiling is in place).
- **symbol_info None fallback:** if MT5 symbol_info is unavailable during a lot_mult scale, place_order
  logs a warning and uses the full base lot. Watch the logs for this warning after enabling.
- **Forming-bar timing:** live `get_recent_m5_closes` fetches from pos 0, so `rsi_cross_down`
  evaluates on the currently-forming M5 bar — it can fire ~1 bar earlier than the backtest (which
  used closed bars only). Verify entry timing on the first live signals; the backtest numbers
  assume closed-bar evaluation.

Do NOT enable until the plan-A test suite passes on the deployed commit.
