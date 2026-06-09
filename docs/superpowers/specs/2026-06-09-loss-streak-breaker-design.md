# Loss-Streak Circuit Breaker (GUARDIAN-R) — Design Spec

**Date:** 2026-06-09
**Status:** Approved (brainstorm) — pending implementation plan
**Motivation:** 2026-06-08 evening bleed — 6 consecutive XAUUSDm SELL Stop-Loss hits
(19:05→22:27), ≈ −$80, gave back the afternoon's gains. Balance $146 → $121.75.
Existing guards did not catch it: `is_cooldown_active` (5-min single-SL cooldown) expired
between the ~5–30-min-spaced losers; `GUARDIAN-J` daily-dollar cap is account-level and slow.
A consecutive-loss signal is the missing, surgical control.

## Goal

Stop a symbol's new entries once it has lost a configurable number of trades in a row
(Stop-Loss exits), for a cooldown window, then automatically re-arm. Purely protective:
it can only **block** entries — it never opens a position and never closes an open one.

## Decided design axes (user-approved)

| Axis | Decision |
|------|----------|
| Streak scope | **Per symbol** (any direction) |
| Trip threshold | **3** consecutive SL exits |
| Action | **Halt new entries + timed cooldown** |
| Cooldown | **60 minutes** (measured from the most-recent SL's `exit_time`) |
| Rollout | **Active on deploy**, config-tunable, **all symbols** |
| Guardian id | **GUARDIAN-R** (R is a free letter; live guards in use: D–Q, S) |

## Architecture

One pure-ish predicate on `RiskManager`, mirroring the existing
`is_cooldown_active` / `is_daily_budget_exhausted` / `is_slip_cooldown_active` pattern
(derive state from `trade_history`, no new persisted state → restart-safe).

### Interface

```python
def is_loss_streak_active(self, symbol,
                          streak_n=LOSS_STREAK_N,
                          cooldown_minutes=LOSS_STREAK_COOLDOWN_MIN,
                          now=None, rows=None) -> bool:
    """[GUARDIAN-R] Block new entries for `cooldown_minutes` after `streak_n`
    consecutive Stop-Loss exits on this symbol. Re-arms automatically: any non-SL
    exit (TP, slip-close, manual) in the top-N window breaks the streak; cooldown
    expiry unblocks even if the window is still all-SL. Fail-open."""
```

- `now` / `rows` are injectable for unit testing (defaults: `datetime.now()` and a DB query).
- `rows` is the top-`streak_n` closed trades for `symbol`, newest first, each a
  `(exit_time, exit_reason)` pair.

### Decision logic

1. If `LOSS_STREAK_BREAKER_ENABLED` is False → return `False` (inert).
2. Load `rows` = last `streak_n` **closed** trades for `symbol`
   (`exit_time IS NOT NULL`, `ORDER BY exit_time DESC LIMIT streak_n`).
3. If `len(rows) < streak_n` → `False` (not enough history).
4. If **every** row is a Stop-Loss (`exit_reason LIKE '%Stop Loss%'`) → streak tripped;
   otherwise → `False` (a non-SL exit in the window broke the streak).
5. Elapsed = `now − parse(rows[0].exit_time)` (rows[0] = most-recent SL).
   - `elapsed < cooldown_minutes` → **block (`True`)**.
   - else → `False` (cooldown expired; re-armed).
6. Any exception → log a warning, return `False` (**fail-open** — never block on a DB error).

### Clock

Use `datetime.now()` and `datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")`, identical to
`is_cooldown_active`. Both read the same `exit_time` column on the same host, so there is no
local-vs-server skew (do **not** use the tick-time clock that `is_slip_cooldown_active` uses —
that compares against MT5 server time, a different reference).

### Wiring

Add the gate to the deterministic pre-flight block in **both** the BUY and SELL scans of
`trade_manager.py`, immediately after GUARDIAN-S (≈ L411 / L503), using `break` (consistent
with the other hard per-symbol blocks in that block):

```python
if agent3.is_loss_streak_active(s):
    log.warning(f"🛑 [GUARDIAN-R] บล็อก! {s} — loss-streak cooldown active"); break
```

### Config (`bot_config.py`, overridable via `settings.txt`)

```python
LOSS_STREAK_BREAKER_ENABLED = bool(conf.get("LOSS_STREAK_BREAKER_ENABLED", True))
LOSS_STREAK_N               = int(conf.get("LOSS_STREAK_N", 3))
LOSS_STREAK_COOLDOWN_MIN    = int(conf.get("LOSS_STREAK_COOLDOWN_MIN", 60))
```

Tunable without redeploy (the bot reads `settings.txt` at config load). Follow the exact
parse idiom already used for `DAILY_LOSS_LIMIT` in `bot_config.py`.

## Behavioral notes (explicit, so expectations are right)

1. **Entry guard, not a position-closer.** It blocks *new* entries only. It does NOT unwind
   already-open layers. A simultaneous pyramid stop-out (multiple layers stopping together —
   the 2026-06-04 scenario) trips the breaker *after the fact*; GUARDIAN-R cannot prevent that
   wipeout, only the re-entry that would follow.
2. **Throttle, not a hard halt.** With a timed cooldown, in a sustained adverse regime the bot
   trades ~once per cooldown window (lose → block 60m → re-arm → lose …) until a TP breaks the
   streak. This is the direct, accepted consequence of choosing a timed cooldown over
   "halt until DIRECTOR regime flips." It bounds the bleed rate; it does not zero it.
3. **A pyramid counts as a streak — intentional.** Under per-symbol scope, 3 layers of one
   thesis stopping together register as a 3-SL streak and trip the breaker. This is a conscious,
   accepted consequence of the per-symbol (not per-entry-event) scope, not an accident.

## Edge cases

- **`[recovered]` Stop-Loss** (`'Hit Stop Loss 🛡️ [recovered]'`, present on 06-08) — matches
  `%Stop Loss%`, so it **counts** as a loss in the streak. Correct: it is still a realized loss.
- **GUARDIAN-M slip-close** (`'GUARDIAN-M: slip …'`, net ≈ −$0.30) — does **not** match
  `%Stop Loss%`, so it does not count *and* it breaks the streak window. Conservative
  (under-blocks). Accepted: a slip-close is a churn artifact, not a thesis failure.
- **Take-Profit / manual exits** — break the streak (window no longer all-SL).
- **Restart-safe** — stateless; recomputed from `trade_history` each tick.
- **Daily rollover** — not needed; "last N" rolls forward naturally.

## Testing

TDD. Pure-path cases via injected `rows` + `now`, **plus** one integration case against a real
temp SQLite DB (the pure path cannot catch a wrong filter / missing `exit_time IS NOT NULL` /
bad `ORDER BY` — the same untested gap `is_cooldown_active` has today).

Pure-path (inject `rows`, `now`):
- 3 SL, most-recent < 60m ago → `True` (blocked)
- 3 SL, most-recent > 60m ago → `False` (cooldown expired)
- 2 SL + 1 TP in window → `False` (streak broken)
- fewer than 3 closed trades → `False`
- `LOSS_STREAK_BREAKER_ENABLED = False` → `False`
- `[recovered]` SL counts as SL (3×recovered → `True`)
- slip-close in window → `False` (does not count, breaks streak)

Integration (temp SQLite seeded with rows):
- Real query returns the correct top-3 newest, ignores `exit_time IS NULL` (open/pyramid)
  rows, and `is_loss_streak_active` trips as expected.
- DB error / unreadable path → fail-open `False`.

## Before-done verification (real-data replay)

Before declaring complete, replay the logic over the **actual** `trade_history` (via SSH/DB
read) and confirm:
- It **trips at the 19:25 XAU SL** on 2026-06-08 (the 3rd consecutive evening SL), which would
  have blocked the 19:35 / 20:05 / 22:27 re-entries (≈ −$40+ saved).
- It does **not** fire spuriously over prior weeks (e.g. 28 May, mixed TP/SL days).
This validates the design against reality and surfaces any `%Stop Loss%` matching surprises
(the `[recovered]` and slip-close edges) against real rows.

## Scope / out of scope

- **Live-only.** GUARDIAN-R is added to `trade_manager` + `risk_manager` only. Porting it to
  `backtest.py` is **deferred** — note that this widens the live/backtest gate gap that the
  SP-C conviction work tracks (alongside GUARDIAN-Q / spread / G/H/I/L). Add "port GUARDIAN-R
  to backtest" to that backlog so it is not silently lost.
- No change to existing guards, lot sizing, or exit logic.
- No new persisted state, no schema change.

## Risks

- **Over-block in choppy-but-mean-reverting regimes** — a real win after 2 SL would have come on
  the blocked 3rd entry. Mitigated by config-tunability (`LOSS_STREAK_N`, cooldown) and the
  real-data replay check above. Conservative by design; bias is toward not trading.
- **Fail-open** means a DB problem silently disables the guard — acceptable (matches every other
  GUARDIAN gate; never block live trading on infrastructure error).
