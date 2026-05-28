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
