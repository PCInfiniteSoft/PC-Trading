# Loss-Streak Circuit Breaker (GUARDIAN-R) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block a symbol's new entries for a cooldown window after N consecutive Stop-Loss exits, then auto re-arm — a per-symbol loss-streak circuit breaker (GUARDIAN-R).

**Architecture:** One DB-derived predicate `RiskManager.is_loss_streak_active` mirroring the existing `is_cooldown_active` / `is_slip_cooldown_active` pattern (no new persisted state → restart-safe). It is injectable (`rows`, `now`, `enabled`) for unit tests and falls back to a `trade_history` query in production. Wired into the deterministic pre-flight block of both BUY and SELL scans in `trade_manager.py`. Active on deploy, tunable via `settings.txt`. Live-only; backtest port deferred.

**Tech Stack:** Python 3.12, sqlite3, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-09-loss-streak-breaker-design.md`

---

## File Structure

- **Modify** `bot_config.py` — add 3 config constants (`LOSS_STREAK_BREAKER_ENABLED`, `LOSS_STREAK_N`, `LOSS_STREAK_COOLDOWN_MIN`) parsed from `settings.txt`, following the `DAILY_LOSS_LIMIT` idiom.
- **Modify** `risk_manager.py` — add module constants + `RiskManager.is_loss_streak_active(...)` method (GUARDIAN-R). Pure decision over injected `rows`/`now`/`enabled`; DB query when `rows is None`.
- **Modify** `trade_manager.py` — call the gate in the BUY pre-flight (~L411, after GUARDIAN-S) and the SELL pre-flight (~L503), passing config from `bot_config`.
- **Create** `tests/test_loss_streak.py` — pure-path unit tests + one integration test against a temp SQLite DB.

Boundary rule: the method takes `enabled`/`streak_n`/`cooldown_minutes` as parameters (the call site supplies them from `bot_config`). `risk_manager.py` does NOT import `bot_config` — matches how `is_daily_budget_exhausted` receives `DAILY_LOSS_LIMIT` from its call site.

---

### Task 1: Config constants in `bot_config.py`

**Files:**
- Modify: `bot_config.py` (after `DAILY_LOSS_LIMIT`, ~line 30)

Config constants are not unit-tested in this codebase (tests mock `bot_config`; it imports
`MetaTrader5` at module top and cannot be imported under pytest without MT5). So this task is
a literal addition + commit, no test — consistent with how `DAILY_LOSS_LIMIT` was added.

- [ ] **Step 1: Add the constants**

In `bot_config.py`, immediately after the `DAILY_LOSS_LIMIT` line (~30):

```python
# [Gate R] Loss-streak circuit breaker — block a symbol's NEW entries after
# LOSS_STREAK_N consecutive Stop-Loss exits, for LOSS_STREAK_COOLDOWN_MIN minutes,
# then auto re-arm. Motivated by 2026-06-08 (6 consecutive XAU SELL SL, -$80).
# Tunable in settings.txt. Active by default; purely protective (only blocks entries).
LOSS_STREAK_BREAKER_ENABLED = conf.get("LOSS_STREAK_BREAKER_ENABLED", "True").strip().lower() in ("1", "true", "yes", "on")
LOSS_STREAK_N               = int(conf.get("LOSS_STREAK_N", 3))
LOSS_STREAK_COOLDOWN_MIN    = int(conf.get("LOSS_STREAK_COOLDOWN_MIN", 60))
```

- [ ] **Step 2: Sanity-check it imports (no MT5 needed for a syntax check)**

Run: `python -c "import ast; ast.parse(open('bot_config.py').read()); print('OK')"`
Expected: `OK` (parses clean — full import needs MT5, not available in dev).

- [ ] **Step 3: Commit**

```bash
git add bot_config.py
git commit -m "config(guardian-r): loss-streak breaker constants (enabled/N/cooldown)"
```

---

### Task 2: `is_loss_streak_active` — pure decision over injected rows

**Files:**
- Modify: `risk_manager.py` (add module constants near `SLIP_COOLDOWN_MINUTES` ~L92, and the method after `is_slip_cooldown_active` ~L368)
- Test: `tests/test_loss_streak.py` (create)

The method's pure core: given `rows` (newest-first list of `(exit_time_str, exit_reason)`),
`now` (a `datetime`), and the three config values, decide block/allow. `rows`/`now` injected
in this task; the DB query path is Task 3.

- [ ] **Step 1: Write the failing tests (pure path)**

```python
# tests/test_loss_streak.py
"""Unit tests for GUARDIAN-R loss-streak circuit breaker (is_loss_streak_active)."""
from datetime import datetime, timedelta
from risk_manager import RiskManager

SL  = "Hit Stop Loss 🛡️"
SLR = "Hit Stop Loss 🛡️ [recovered]"
TP  = "Hit Take Profit 🎯"
SLIP = "GUARDIAN-M: slip 421pts"
NOW = datetime(2026, 6, 8, 20, 0, 0)

def _row(reason, minutes_ago):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return (t, reason)

def _rm():
    return RiskManager(db_path=":memory:")

def test_three_sl_within_cooldown_blocks():
    rows = [_row(SL, 5), _row(SL, 30), _row(SL, 50)]   # newest 5m ago
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is True

def test_three_sl_cooldown_expired_allows():
    rows = [_row(SL, 61), _row(SL, 80), _row(SL, 95)]  # newest 61m ago > 60m
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_tp_in_window_breaks_streak():
    rows = [_row(SL, 5), _row(TP, 20), _row(SL, 40)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_fewer_than_n_trades_allows():
    rows = [_row(SL, 5), _row(SL, 30)]                  # only 2
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False

def test_disabled_flag_allows():
    rows = [_row(SL, 5), _row(SL, 30), _row(SL, 50)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows, enabled=False) is False

def test_recovered_sl_counts_as_loss():
    rows = [_row(SLR, 5), _row(SLR, 30), _row(SLR, 50)]
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is True

def test_slip_close_does_not_count_and_breaks_streak():
    rows = [_row(SLIP, 5), _row(SL, 30), _row(SL, 50)]  # newest is a slip-close
    assert _rm().is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=NOW, rows=rows) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_loss_streak.py -v`
Expected: FAIL — `AttributeError: 'RiskManager' object has no attribute 'is_loss_streak_active'`

- [ ] **Step 3: Write the module constants + method**

In `risk_manager.py`, after the `SLIP_COOLDOWN_MINUTES = 5` block (~L92):

```python
# ── Loss-streak circuit breaker (Gate R, 2026-06-09) ──────────────
# Block a symbol's NEW entries after LOSS_STREAK_N consecutive Stop-Loss exits,
# for LOSS_STREAK_COOLDOWN_MIN minutes from the most-recent SL, then auto re-arm.
# Any non-SL exit (TP, slip-close, manual) in the top-N window breaks the streak.
# Defaults here are fallbacks; the live values come from bot_config via the call site.
LOSS_STREAK_N            = 3
LOSS_STREAK_COOLDOWN_MIN = 60
```

After `is_slip_cooldown_active` (~L368), add the method:

```python
    def is_loss_streak_active(self, symbol, streak_n=LOSS_STREAK_N,
                              cooldown_minutes=LOSS_STREAK_COOLDOWN_MIN,
                              enabled=True, now=None, rows=None):
        """[GUARDIAN-R] Block NEW entries on `symbol` for `cooldown_minutes` after
        `streak_n` consecutive Stop-Loss exits. Auto re-arms: any non-SL exit (TP,
        slip-close, manual) in the top-N window breaks the streak; cooldown expiry
        unblocks even if the window is still all-SL.

        Purely protective — only blocks new entries; never closes an open position.
        `rows`: newest-first list of (exit_time_str, exit_reason) — injectable for
        tests; defaults to a trade_history query. `now`: datetime, injectable
        (defaults to datetime.now(), matching is_cooldown_active's clock since both
        read the same exit_time column). Fail-open."""
        import logging
        log = logging.getLogger("System")
        try:
            if not enabled:
                return False
            if now is None:
                now = datetime.now()
            if rows is None:
                rows = self._recent_exits(symbol, streak_n)   # Task 3
            if len(rows) < streak_n:
                return False
            # Streak = the top-N exits are ALL Stop-Loss
            if not all("Stop Loss" in (r[1] or "") for r in rows):
                return False
            last_sl_time = datetime.strptime(rows[0][0], "%Y-%m-%d %H:%M:%S")
            elapsed_min = (now - last_sl_time).total_seconds() / 60.0
            if elapsed_min < cooldown_minutes:
                self._set_blocked(
                    f"Loss-streak {streak_n}x SL — {elapsed_min:.1f}m<{cooldown_minutes}m")
                log.warning(
                    f"🛑 [GUARDIAN-R] Blocked {symbol} — {streak_n} consecutive SL, "
                    f"last {elapsed_min:.1f}m ago (< {cooldown_minutes}m cooldown)")
                return True
            return False
        except Exception as e:
            log.warning(f"⚠️ [GUARDIAN-R] loss-streak check error: {e}")
            return False   # fail-open
```

> NOTE: `_recent_exits` does not exist yet — it is added in Task 3. These pure-path tests
> never hit it (they always pass `rows=`), so Task 2 passes without it. Do not stub it here.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_loss_streak.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add risk_manager.py tests/test_loss_streak.py
git commit -m "feat(guardian-r): is_loss_streak_active pure decision + unit tests"
```

---

### Task 3: DB query path `_recent_exits` + integration test

**Files:**
- Modify: `risk_manager.py` (add `_recent_exits` helper near `is_loss_streak_active`)
- Test: `tests/test_loss_streak.py` (add integration cases)

The pure tests cannot catch a wrong filter / missing `exit_time IS NOT NULL` / bad `ORDER BY`
(the same untested gap `is_cooldown_active` has). This task adds the real query and exercises
it against a temp SQLite DB.

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/test_loss_streak.py`:

```python
import sqlite3, pathlib

def _seed_db(path, trades):
    """trades: list of (symbol, exit_time_or_None, exit_reason, net_profit)."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE trade_history (
        ticket INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, order_type TEXT,
        lot_size REAL, entry_time TEXT, entry_price REAL, entry_reason TEXT,
        slippage REAL, exit_time TEXT, exit_price REAL, net_profit REAL,
        max_floating_profit REAL, max_floating_loss REAL, exit_reason TEXT,
        balance_after_trade REAL)""")
    for sym, xt, xr, net in trades:
        conn.execute("INSERT INTO trade_history (symbol, exit_time, exit_reason, net_profit) "
                     "VALUES (?,?,?,?)", (sym, xt, xr, net))
    conn.commit(); conn.close()

def test_query_returns_top3_newest_and_trips(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", "2026-06-08 19:05:00", SL, -12.98),
        ("XAUUSDm", "2026-06-08 19:25:00", SL, -15.54),
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),   # newest SL
        ("BTCUSDm", "2026-06-08 19:36:00", SL, -5.0),     # other symbol, ignore
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)   # 5m after newest XAU SL
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is True

def test_query_ignores_open_positions(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", None,                  SL, 0.0),      # open (exit_time NULL) — must be ignored
        ("XAUUSDm", "2026-06-08 19:05:00", SL, -12.98),
        ("XAUUSDm", "2026-06-08 19:25:00", SL, -15.54),
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is True

def test_query_tp_in_top3_allows(tmp_path):
    db = str(tmp_path / "t.db")
    _seed_db(db, [
        ("XAUUSDm", "2026-06-08 18:00:00", SL, -12.0),
        ("XAUUSDm", "2026-06-08 19:25:00", TP, +13.0),    # a win in the top-3
        ("XAUUSDm", "2026-06-08 19:35:00", SL, -12.99),
    ])
    rm = RiskManager(db_path=db)
    now = datetime(2026, 6, 8, 19, 40, 0)
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60, now=now) is False

def test_db_error_fails_open(tmp_path):
    rm = RiskManager(db_path=str(tmp_path / "does_not_exist_dir" / "nope.db"))
    # Bad path → sqlite error inside _recent_exits → fail-open False
    assert rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                    now=datetime(2026,6,8,20,0,0)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_loss_streak.py -k query -v`
Expected: FAIL — `AttributeError: 'RiskManager' object has no attribute '_recent_exits'`

- [ ] **Step 3: Implement `_recent_exits`**

In `risk_manager.py`, directly above `is_loss_streak_active`:

```python
    def _recent_exits(self, symbol, n):
        """Return the most-recent `n` CLOSED trades for `symbol` as newest-first
        (exit_time, exit_reason) tuples. Open positions (exit_time NULL) excluded."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT exit_time, exit_reason
                FROM trade_history
                WHERE symbol = ? AND exit_time IS NOT NULL
                ORDER BY exit_time DESC
                LIMIT ?
            """, (symbol, n))
            return cur.fetchall()
        finally:
            conn.close()
```

> The `test_db_error_fails_open` case relies on `is_loss_streak_active`'s `try/except`
> wrapping the `_recent_exits` call — a bad path raises `sqlite3.OperationalError`, caught,
> returns `False`. Do not add a separate try/except inside `_recent_exits`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_loss_streak.py -v`
Expected: PASS (11 passed — 7 pure + 4 integration).

- [ ] **Step 5: Commit**

```bash
git add risk_manager.py tests/test_loss_streak.py
git commit -m "feat(guardian-r): _recent_exits DB query + integration tests"
```

---

### Task 4: Wire GUARDIAN-R into the BUY + SELL pre-flight

**Files:**
- Modify: `trade_manager.py` — BUY scan (~L411, after the GUARDIAN-S line) and SELL scan
  (~L503, after the GUARDIAN-S line).
- Modify: `trade_manager.py` — import the config constants from `bot_config` (find the
  existing `from bot_config import ... DAILY_LOSS_LIMIT ...` line and extend it).

Wiring mirrors GUARDIAN-S exactly (same block, `break`). The decision logic is already fully
tested at the `risk_manager` level (Tasks 2–3) — this task is the integration; verification is
the real-data replay in Task 5 plus the full suite staying green.

- [ ] **Step 1: Extend the bot_config import in `trade_manager.py`**

Find the existing import that brings in `DAILY_LOSS_LIMIT` (grep `from bot_config import`).
Add the three new names to it, e.g.:

```python
from bot_config import (SYMBOLS_CONFIG, MAGIC_NUMBER, DAILY_LOSS_LIMIT,
                        LOSS_STREAK_BREAKER_ENABLED, LOSS_STREAK_N, LOSS_STREAK_COOLDOWN_MIN)
```

> Match the ACTUAL existing import form (it may be a single line or already multi-name). Do
> not invent a second import statement — extend the existing one.

- [ ] **Step 2: Add the gate to the BUY pre-flight**

In the BUY scan, immediately after the GUARDIAN-S line (~L411):

```python
                        if agent3.is_slip_cooldown_active(s):
                            log.warning(f"🛑 [GUARDIAN-S] บล็อก! {s} BUY — slip-close cooldown active"); break
                        if agent3.is_loss_streak_active(s, streak_n=LOSS_STREAK_N,
                                cooldown_minutes=LOSS_STREAK_COOLDOWN_MIN,
                                enabled=LOSS_STREAK_BREAKER_ENABLED):
                            log.warning(f"🛑 [GUARDIAN-R] บล็อก! {s} BUY — loss-streak cooldown active"); break
```

- [ ] **Step 3: Add the gate to the SELL pre-flight**

In the SELL scan, immediately after the GUARDIAN-S line (~L503):

```python
                        if agent3.is_slip_cooldown_active(s):
                            log.warning(f"🛑 [GUARDIAN-S] บล็อก! {s} SELL — slip-close cooldown active"); break
                        if agent3.is_loss_streak_active(s, streak_n=LOSS_STREAK_N,
                                cooldown_minutes=LOSS_STREAK_COOLDOWN_MIN,
                                enabled=LOSS_STREAK_BREAKER_ENABLED):
                            log.warning(f"🛑 [GUARDIAN-R] บล็อก! {s} SELL — loss-streak cooldown active"); break
```

- [ ] **Step 4: Verify the full suite still passes (no regression)**

Run: `pytest -q`
Expected: all green (prior count + 11 new from `tests/test_loss_streak.py`).

- [ ] **Step 5: Verify the wiring landed in both scans**

Run: `grep -n "GUARDIAN-R" trade_manager.py`
Expected: 2 matches (one in the BUY block, one in the SELL block).

- [ ] **Step 6: Commit**

```bash
git add trade_manager.py
git commit -m "feat(guardian-r): wire loss-streak breaker into BUY+SELL pre-flight"
```

---

### Task 5: Before-done real-data replay + docs

**Files:**
- Create (local, throwaway, NOT committed): `tools/replay_guardian_r.py`
- Modify: `docs/superpowers/specs/2026-06-07-strategy-separation-design.md` OR the SP-C
  memory backlog — add "port GUARDIAN-R to backtest" (deferred live/backtest gap item).

This is the spec's before-done verification gate: prove the logic trips on the real 06-08
evening streak and does NOT fire spuriously in prior weeks, using the actual `trade_history`.

- [ ] **Step 1: Write the replay script (runs the real predicate over real rows)**

```python
# tools/replay_guardian_r.py  — read-only replay over the live trade_history
import sys, sqlite3
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from risk_manager import RiskManager

DB = sys.argv[1] if len(sys.argv) > 1 else "trading_history.db"
rm = RiskManager(db_path=DB)
conn = sqlite3.connect(DB); cur = conn.cursor()
# Every closed XAU trade, oldest-first; at each one, ask "would R have blocked the NEXT entry?"
rows = cur.execute("SELECT exit_time, exit_reason FROM trade_history "
                   "WHERE symbol='XAUUSDm' AND exit_time IS NOT NULL "
                   "ORDER BY exit_time ASC").fetchall()
for i in range(len(rows)):
    window = list(reversed(rows[:i+1]))[:3]          # newest-first top-3 as of this point
    now = datetime.strptime(rows[i][0], "%Y-%m-%d %H:%M:%S")
    blocked = rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=now, rows=window)
    flag = "  <== BLOCK next entry" if blocked else ""
    print(rows[i][0], rows[i][1][:24], flag)
conn.close()
```

- [ ] **Step 2: Run it against the real DB on the server (read-only)**

Do NOT scp the script — pipe it to the server's python over stdin (CLAUDE.md rule):

```bash
ssh Administrator@100.106.19.75 "cd C:\Users\Administrator\Desktop\PC-Trading && python -" < tools/replay_guardian_r.py
```

Expected: the 2026-06-08 evening rows show the breaker arming at the **3rd consecutive SL
(19:25 row → blocks the next, 19:35)**, and prior-week mixed TP/SL days do NOT show a block.
If it fires on a day that was not actually a 3-in-a-row SL streak, STOP and re-check the
`Stop Loss` match / `ORDER BY` before declaring done.

- [ ] **Step 3: Record the SP-C backlog item (live/backtest gap)**

GUARDIAN-R is live-only. Append a one-line note to the SP-C live-gate backlog (in the
strategy-separation spec's SP-C section, alongside GUARDIAN-Q / spread / G/H/I/L):
`- [ ] Port GUARDIAN-R (loss-streak breaker) to backtest.py for live/backtest parity.`

- [ ] **Step 4: Commit the docs note**

```bash
git add docs/superpowers/specs/2026-06-07-strategy-separation-design.md
git commit -m "docs(sp-c): track GUARDIAN-R backtest port in live-gate backlog"
```

> `tools/replay_guardian_r.py` is a throwaway verification artifact — `*.py` under `tools/`
> may be committed if useful, but it is not required. Do not commit it if `tools/` is gitignored.

---

## Self-Review checklist (run before handing off)

- [ ] Spec coverage: per-symbol scope (query filters `symbol=?`, Task 3) ✔; threshold N
  (`streak_n`, Tasks 2–4) ✔; timed cooldown from most-recent SL (Task 2 elapsed calc) ✔;
  active+config-tunable (Task 1 + call-site params, Task 4) ✔; `[recovered]` counts /
  slip-close doesn't (Task 2 tests) ✔; `exit_time IS NOT NULL` filter + integration test
  (Task 3) ✔; fail-open (Task 2 + Task 3 db-error test) ✔; real-data replay before done
  (Task 5) ✔; live-only + backtest-port backlog (Task 5) ✔.
- [ ] No placeholders: all code blocks complete; `_recent_exits` defined (Task 3) before its
  only production caller path is exercised; no "add error handling" hand-waves.
- [ ] Type consistency: `is_loss_streak_active(symbol, streak_n, cooldown_minutes, enabled,
  now, rows)` identical across Tasks 2/3/4; `rows` always newest-first `(exit_time, exit_reason)`;
  `_recent_exits(symbol, n)` returns that exact shape.
- [ ] Behavioral notes from spec (entry-guard-only, throttle-not-halt, pyramid-counts) need no
  code — they are documented consequences, covered by the design comments in Task 2's docstring.

## Execution note

Highest-risk task is Task 4 (touches live order-gating code). The gate is `break`-based and
purely subtractive (it can only *prevent* an entry), so the worst-case failure mode is
over-blocking, never an unintended trade. Task 5's real-data replay is the acceptance gate:
the breaker must trip on the real 06-08 streak and stay silent on non-streak days.
