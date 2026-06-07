# Strategy Separation — Plan A (per-symbol profile + st3 port) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move per-symbol strategy logic out of inline `trade_manager.py` branches into a per-symbol `strategy` profile (behavior-preserving, parity-gated), then add the st3 trend-sell SELL path to XAU behind an OFF-by-default toggle.

**Architecture:** A new `strategy_profile.py` module owns the per-symbol profile (entry paths, guard toggles, trend-sell params) sourced from `SYMBOLS_CONFIG`. `trade_manager.py` reads the profile instead of hardcoding `"BTC" in s` / `xau_buy_only`. The shared `RiskManager` guard chain is unchanged. The st3 trigger (`rsi_cross_down` at level 50, gated on D1 DOWNTREND) is ported from `backtest.py` into a live `trend_sell_signal()` and wired as a second SELL entry path for XAU only.

**Tech Stack:** Python 3, pytest, MetaTrader5 API, existing `bot_config.SYMBOLS_CONFIG`, `risk_manager.RiskManager`.

**Scope note:** This is plan-A of three (see `docs/superpowers/specs/2026-06-07-strategy-separation-design.md`). It must leave the live bot behaving **identically** until the `xau_trend_sell_enabled` toggle is manually turned on. Plan-B (dynamic lot) and plan-C (conviction table) are written separately after A lands, when the A→B interface is concrete.

---

## File Structure

- **Create** `strategy_profile.py` — builds and exposes the per-symbol `strategy` profile; pure functions, no MT5/IO. One responsibility: answer "what paths/guards/params apply to this symbol".
- **Modify** `bot_config.py` — add a `"strategy"` block to each entry in `SYMBOLS_CONFIG`.
- **Modify** `trade_manager.py` — replace inline per-symbol branches with profile lookups; add the trend-sell SELL path for XAU.
- **Create** `test_strategy_profile.py` — unit + characterization tests for the profile and the ported predicate.

The trend-sell predicate (`rsi_cross_down`) already lives in `backtest.py:93`. To avoid duplication (DRY), plan-A imports the **existing** `backtest.rsi_cross_down` rather than re-implementing it; the live wrapper only assembles the M5 close series and applies the D1-DOWNTREND gate.

---

## Task 1: Add `strategy` profile to SYMBOLS_CONFIG (config only, no behavior change)

**Files:**
- Modify: `bot_config.py:48-112` (the two `SYMBOLS_CONFIG` entries)
- Test: `test_strategy_profile.py`

This task only adds data. The profile mirrors today's hardcoded behavior so nothing changes
until later tasks read it.

- [ ] **Step 1: Write the failing test**

```python
# test_strategy_profile.py
from bot_config import SYMBOLS_CONFIG


def test_btc_profile_mirrors_current_behavior():
    p = SYMBOLS_CONFIG["BTCUSDm"]["strategy"]
    assert p["entry_paths"] == ["mean_reversion"]          # BTC: no trend-sell yet
    assert p["guards"]["xau_buy_only"] is False
    assert p["guards"]["score_blacklist"] == {8}
    assert p["xau_trend_sell_enabled"] is False


def test_xau_profile_mirrors_current_behavior():
    p = SYMBOLS_CONFIG["XAUUSDm"]["strategy"]
    # Behavior-preserving: XAU still buy-only until A2 toggle flips on.
    assert p["entry_paths"] == ["mean_reversion"]
    assert p["guards"]["xau_buy_only"] is True
    assert p["guards"]["score_blacklist"] == {8}
    assert p["xau_trend_sell_enabled"] is False
    assert p["trend_sell"] == {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_strategy_profile.py -v`
Expected: FAIL with `KeyError: 'strategy'`

- [ ] **Step 3: Add the profile block to both symbols**

In `bot_config.py`, inside the `"BTCUSDm"` dict (after the existing keys, before the closing `},`):

```python
        # ── Strategy profile (SP-A) — per-symbol logic, read by trade_manager ──
        "strategy": {
            "entry_paths": ["mean_reversion"],
            "guards": {
                "xau_buy_only": False,
                "score_blacklist": {8},
            },
            # st3 trend-sell config (used only when xau_trend_sell_enabled is True)
            "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
            "xau_trend_sell_enabled": False,
        },
```

Inside the `"XAUUSDm"` dict, add the same block but with `"xau_buy_only": True`:

```python
        "strategy": {
            "entry_paths": ["mean_reversion"],
            "guards": {
                "xau_buy_only": True,
                "score_blacklist": {8},
            },
            "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
            "xau_trend_sell_enabled": False,
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_strategy_profile.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bot_config.py test_strategy_profile.py
git commit -m "feat(strategy): add per-symbol strategy profile to SYMBOLS_CONFIG (no behavior change)"
```

---

## Task 2: `strategy_profile.py` accessor module

**Files:**
- Create: `strategy_profile.py`
- Test: `test_strategy_profile.py`

Thin, pure accessors so `trade_manager` never reaches into nested dicts directly. Defaults
keep callers safe if a symbol lacks a profile (mirrors current `.get(..., default)` style).

- [ ] **Step 1: Write the failing test**

```python
# test_strategy_profile.py  (append)
import strategy_profile as sp


def test_get_profile_returns_dict():
    assert sp.get_profile("XAUUSDm")["guards"]["xau_buy_only"] is True


def test_has_path():
    assert sp.has_path("BTCUSDm", "mean_reversion") is True
    assert sp.has_path("BTCUSDm", "trend_sell") is False


def test_guard_enabled_defaults_false_for_unknown_symbol():
    assert sp.guard_enabled("NOPE", "xau_buy_only") is False


def test_trend_sell_enabled():
    assert sp.trend_sell_enabled("XAUUSDm") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_strategy_profile.py -k "profile or path or guard or trend_sell_enabled" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategy_profile'`

- [ ] **Step 3: Implement the module**

```python
# strategy_profile.py
"""Per-symbol strategy profile accessors (SP-A).

Pure functions over bot_config.SYMBOLS_CONFIG[symbol]["strategy"]. No MT5/IO.
Centralizes the per-symbol logic that used to be inline in trade_manager.py.
"""
from bot_config import SYMBOLS_CONFIG

_EMPTY = {
    "entry_paths": ["mean_reversion"],
    "guards": {},
    "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
    "xau_trend_sell_enabled": False,
}


def get_profile(symbol: str) -> dict:
    return SYMBOLS_CONFIG.get(symbol, {}).get("strategy", _EMPTY)


def has_path(symbol: str, path: str) -> bool:
    return path in get_profile(symbol).get("entry_paths", [])


def guard_enabled(symbol: str, guard: str) -> bool:
    return bool(get_profile(symbol).get("guards", {}).get(guard, False))


def score_blacklist(symbol: str) -> set:
    return get_profile(symbol).get("guards", {}).get("score_blacklist", set())


def trend_sell_enabled(symbol: str) -> bool:
    return bool(get_profile(symbol).get("xau_trend_sell_enabled", False))


def trend_sell_cfg(symbol: str) -> dict:
    return get_profile(symbol).get("trend_sell", _EMPTY["trend_sell"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_strategy_profile.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add strategy_profile.py test_strategy_profile.py
git commit -m "feat(strategy): strategy_profile.py pure accessors over SYMBOLS_CONFIG"
```

---

## Task 3: Route the inline per-symbol guards through the profile (parity gate)

**Files:**
- Modify: `trade_manager.py:417` (GUARDIAN-N: `"BTC" in s` H4 check), `:426` (GUARDIAN-O: `"BTC" in s` MACD), `:453` & `:532` (GUARDIAN-H: `is_score_blacklisted`)
- Test: `test_strategy_profile.py`

The behavior must not change — this only swaps the *source* of the per-symbol decision from a
hardcoded string check to the profile. The score-blacklist already lives in `RiskManager`, so
keep calling `agent3.is_score_blacklisted`; the profile becomes the single source of truth for
the `{8}` band by passing it through. The `"BTC" in s` guards (N/O) stay BTC-only because only
BTC has them — encode that by gating on `sp.has_path(s, "mean_reversion")` plus a new
`btc_momentum_guards` flag rather than the string check.

- [ ] **Step 1: Write the characterization test (pins current behavior)**

```python
# test_strategy_profile.py  (append)
import strategy_profile as sp


def test_btc_has_momentum_guards_xau_does_not():
    # GUARDIAN-N/O are BTC-only today; profile must preserve that exactly.
    assert sp.guard_enabled("BTCUSDm", "btc_momentum_guards") is True
    assert sp.guard_enabled("XAUUSDm", "btc_momentum_guards") is False


def test_score_blacklist_source_is_profile():
    assert sp.score_blacklist("BTCUSDm") == {8}
    assert sp.score_blacklist("XAUUSDm") == {8}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_strategy_profile.py -k "momentum or blacklist_source" -v`
Expected: FAIL — `btc_momentum_guards` not in profile yet

- [ ] **Step 3: Add the flag to BTC profile, then swap the inline checks**

In `bot_config.py` BTC `strategy.guards`, add `"btc_momentum_guards": True`. (Do NOT add it to
XAU — its absence defaults False, preserving today's behavior.)

In `trade_manager.py`, replace line 417:

```python
                        if agent3.is_xau_h4_downtrend(s, macro_data.get('h4_trend', 'N/A')):
                            log.warning(f"🛑 [GUARDIAN-I] บล็อก! {s} BUY — H4 DOWNTREND"); break
                        if "DOWNTREND" in str(macro_data.get('d1_trend', '')):
                            log.warning(f"🛑 [GUARDIAN-L] บล็อก! {s} BUY — D1 DOWNTREND"); break
                        if sp.guard_enabled(s, "btc_momentum_guards") and "UPTREND" not in str(macro_data.get('h4_trend', '')):
                            log.warning(f"🛑 [GUARDIAN-N] บล็อก! {s} BUY — H4 ไม่ใช่ UPTREND ({macro_data.get('h4_trend', 'N/A')})"); break
```

Replace line 426:

```python
                        if sp.guard_enabled(s, "btc_momentum_guards") and scout.get('macd_signal') == "BEARISH":
                            log.warning(f"🛑 [GUARDIAN-O] บล็อก! {s} BUY — MACD BEARISH (momentum ยังลง)"); break
```

Add the import at the top of `trade_manager.py` (next to the other local imports):

```python
import strategy_profile as sp
```

(Leave `agent3.is_score_blacklisted` calls as-is; the `{8}` set is already centralized inside
`RiskManager`, and the profile mirrors it for documentation. No code change at :453/:532.)

- [ ] **Step 4: Run the full test suite to confirm parity**

Run: `python -m pytest test_strategy_profile.py test_backtest.py -v`
Expected: PASS (no regressions; profile tests green)

- [ ] **Step 5: Manual parity check (the gate)**

Confirm the swap is behavior-identical by reading the diff: GUARDIAN-N/O now fire **iff**
`btc_momentum_guards` is True, which is True only for BTC — exactly the old `"BTC" in s`. No
other guard changed.

Run: `git diff trade_manager.py` and verify only lines 417/426 region changed + one import.

- [ ] **Step 6: Commit**

```bash
git add bot_config.py trade_manager.py test_strategy_profile.py
git commit -m "refactor(strategy): route BTC momentum guards through profile (parity-preserving)"
```

---

## Task 4: Port the st3 trend-sell predicate to a live wrapper

**Files:**
- Create wrapper in: `strategy_profile.py`
- Test: `test_strategy_profile.py`
- Reuse: `backtest.rsi_cross_down` (`backtest.py:93`)

A pure function that answers "does st3 fire on this symbol right now?" given the recent M5
closes and the D1 trend. No MT5 calls inside — the caller passes data in, keeping it testable.

- [ ] **Step 1: Write the failing test**

```python
# test_strategy_profile.py  (append)
import pandas as pd
import strategy_profile as sp


def _closes_crossing_down_through_50():
    # RSI engineered to cross from >50 to <50 on the last bar (mirrors backtest test).
    ups = [100 + i for i in range(20)]      # strong uptrend -> RSI well above 50
    downs = [120 - 3 * i for i in range(8)]  # sharp drop -> RSI crosses below 50
    return pd.DataFrame({"close": ups + downs})


def test_trend_sell_fires_on_rsi_cross_in_downtrend():
    df = _closes_crossing_down_through_50()
    assert sp.trend_sell_signal("XAUUSDm", df, d1_trend="DOWNTREND") is True


def test_trend_sell_blocked_when_d1_not_downtrend():
    df = _closes_crossing_down_through_50()
    assert sp.trend_sell_signal("XAUUSDm", df, d1_trend="UPTREND") is False


def test_trend_sell_blocked_when_toggle_off_symbol_btc():
    df = _closes_crossing_down_through_50()
    # BTC has no trend_sell path; signal must be False regardless of price action.
    assert sp.trend_sell_signal("BTCUSDm", df, d1_trend="DOWNTREND") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_strategy_profile.py -k trend_sell_ -v`
Expected: FAIL — `AttributeError: module 'strategy_profile' has no attribute 'trend_sell_signal'`

- [ ] **Step 3: Implement the wrapper**

```python
# strategy_profile.py  (append)
import backtest  # reuse the validated rsi_cross_down predicate (DRY)


def trend_sell_signal(symbol: str, m5_closes, d1_trend: str) -> bool:
    """st3 trigger for live use: RSI cross-down through `rsi_level`, gated on D1 DOWNTREND.

    `m5_closes` is a DataFrame with a 'close' column (most recent bar last), mirroring the
    backtest `m5_slice`. Returns False unless the symbol's profile lists the trend_sell path.
    """
    if not has_path(symbol, "trend_sell"):
        return False
    if str(d1_trend) != "DOWNTREND":
        return False
    cfg = trend_sell_cfg(symbol)
    if cfg.get("trigger") != "rsi":
        return False
    return bool(backtest.rsi_cross_down(m5_closes, level=cfg.get("rsi_level", 50.0)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_strategy_profile.py -k trend_sell_ -v`
Expected: PASS (3 passed). Note: `test_trend_sell_blocked_when_toggle_off_symbol_btc` passes
because BTC's `entry_paths` lacks `"trend_sell"`.

- [ ] **Step 5: Commit**

```bash
git add strategy_profile.py test_strategy_profile.py
git commit -m "feat(strategy): port st3 rsi-cross-down trend-sell predicate to live wrapper"
```

---

## Task 5: Enable `trend_sell` path on XAU profile (still gated OFF by toggle)

**Files:**
- Modify: `bot_config.py` XAU `strategy`
- Test: `test_strategy_profile.py`

Adds `"trend_sell"` to XAU's `entry_paths` so the predicate can fire, but `trend_sell_signal`
still depends on the live wiring honoring `xau_trend_sell_enabled` (Task 6). Path presence and
the runtime toggle are deliberately separate: the path declares capability, the toggle controls
rollout.

- [ ] **Step 1: Update the Task 1 XAU assertion and add a path test**

```python
# test_strategy_profile.py — modify test_xau_profile_mirrors_current_behavior:
    assert p["entry_paths"] == ["mean_reversion", "trend_sell"]

# append:
def test_xau_declares_trend_sell_path_but_toggle_off():
    assert sp.has_path("XAUUSDm", "trend_sell") is True
    assert sp.trend_sell_enabled("XAUUSDm") is False   # rollout still OFF
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_strategy_profile.py -k "xau" -v`
Expected: FAIL — `entry_paths` still `["mean_reversion"]`

- [ ] **Step 3: Add the path**

In `bot_config.py` XAU `strategy`, change:

```python
            "entry_paths": ["mean_reversion", "trend_sell"],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_strategy_profile.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add bot_config.py test_strategy_profile.py
git commit -m "feat(strategy): declare trend_sell path on XAU profile (toggle still OFF)"
```

---

## Task 6: Wire the XAU trend-sell entry into the trade_manager SELL block (guarded rollout)

**Files:**
- Modify: `trade_manager.py:478-539` (SELL block, before the mean-reversion AI path)
- Modify: `place_order` call site to apply `lot_mult` for trend-sell entries
- Test: manual + existing suite (the live loop is MT5/async-coupled; logic is unit-tested in
  Tasks 4-5, so this task is integration wiring verified by the toggle being OFF)

The trend-sell path runs as a second SELL entry that fires before the mean-reversion AI call,
mirroring `backtest.py:575`. It is fully inert while `xau_trend_sell_enabled` is False, so the
live bot is byte-for-byte unchanged until the toggle is manually set.

- [ ] **Step 1: Add the trend-sell branch at the top of the SELL block**

In `trade_manager.py`, immediately after the SELL-layer guard chain establishes `macro_data`
(after line 480, before the deterministic guards at 487), insert:

```python
                    # ── Trend-sell (st3) path — XAU only, OFF until toggle flipped ──
                    if sp.trend_sell_enabled(s) and sp.has_path(s, "trend_sell"):
                        m5_closes = adv.get_recent_m5_closes(s, bars=120)  # DataFrame['close']
                        if sp.trend_sell_signal(s, m5_closes, macro_data.get('d1_trend', '')):
                            log.info(f"📉 [TREND-SELL] st3 fired for {s} (D1 DOWNTREND, RSI cross 50)")
                            tick = mt5.symbol_info_tick(s)
                            if tick is not None and not shared_state.TRADE_LAYERS.get(s, {}).get("sell", [False]*5)[i]:
                                ts_mult = sp.trend_sell_cfg(s).get("lot_mult", 0.5)
                                if place_order(s, "SELL", tick.bid, rsi, f"TS:st3:L{i+1}",
                                               extra={"entry_reason": "trend_sell",
                                                      "scout_score": None, "lot_mult": ts_mult}):
                                    async with shared_state.trade_layers_lock:
                                        shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True
                                    break   # bar claimed by trend-sell — no mean-reversion fall-through
```

- [ ] **Step 2: Add `get_recent_m5_closes` helper to the indicators module**

Find the module bound to `adv` (search `adv = ` in `trade_manager.py`). Add a helper that
returns the last `bars` M5 closes as a DataFrame with a `'close'` column:

```python
def get_recent_m5_closes(symbol, bars=120):
    import pandas as pd, MetaTrader5 as mt5
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars)
    if rates is None or len(rates) == 0:
        return pd.DataFrame({"close": []})
    return pd.DataFrame({"close": [r["close"] for r in rates]})
```

- [ ] **Step 3: Apply `lot_mult` in `place_order`**

In `trade_manager.py` `place_order` (around line 750 where `lot = SYMBOLS_CONFIG[symbol]["lot"]`),
honor an optional multiplier from `extra`:

```python
    lot = SYMBOLS_CONFIG[symbol]["lot"]
    _mult = (extra or {}).get("lot_mult", 1.0)
    lot = round(lot * _mult, 2)
```

(This `lot_mult` is also the seam plan-B will replace with risk-per-trade sizing — keep the
argument name stable.)

- [ ] **Step 4: Verify the bot is unchanged with toggle OFF**

Run: `python -m pytest test_strategy_profile.py test_backtest.py -v`
Expected: PASS. Then confirm by inspection that `sp.trend_sell_enabled("XAUUSDm")` is False, so
the new branch never executes in production yet.

- [ ] **Step 5: Commit**

```bash
git add trade_manager.py
git commit -m "feat(strategy): wire XAU trend-sell entry path (inert until xau_trend_sell_enabled)"
```

---

## Task 7: Document the rollout switch

**Files:**
- Modify: `CLAUDE.md` (add a "Strategy rollout" note)

- [ ] **Step 1: Add rollout instructions**

Append to `CLAUDE.md` under a new `## Strategy Rollout` heading:

```markdown
## Strategy Rollout

- **XAU trend-sell (st3):** disabled by default. To enable live XAU shorts, set
  `SYMBOLS_CONFIG["XAUUSDm"]["strategy"]["xau_trend_sell_enabled"] = True` in `bot_config.py`,
  deploy, then monitor `#SELLdn`, `winner_MFE`, and XAU SELL realized PnL before raising
  `trend_sell.lot_mult` from 0.5.
- Do NOT enable until the plan-A parity tests pass on the deployed commit.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: XAU trend-sell rollout switch + monitoring checklist"
```

---

## Self-Review Notes

- **Spec coverage:** A1 refactor = Tasks 1-3 (profile + parity gate); A2 st3 port = Tasks 4-6;
  guarded rollout = Tasks 5-7 (toggle OFF, lot_mult 0.5, monitoring doc). A→B seam = `lot_mult`
  in `place_order` (Task 6 Step 3).
- **Parity gate:** Task 3 swaps source-of-truth only; the `btc_momentum_guards` flag reproduces
  `"BTC" in s` exactly. Manual diff check is the gate (live loop can't be replayed offline; unit
  characterization + diff review is the achievable equivalent).
- **DRY:** trend-sell predicate reused from `backtest.rsi_cross_down`, not re-implemented.
- **Deferred to plan-B/C:** risk-per-trade sizing replaces `lot_mult`; conviction table feeds
  the same seam. Those plans are written after A lands.

## Open dependency

`adv.get_recent_m5_closes` (Task 6 Step 2) assumes the `adv` module wraps MT5 rate access. If
`adv` has no place for it, add the helper to whichever module already calls
`mt5.copy_rates_from_pos` and import it — do not scatter MT5 calls into `trade_manager.py`
directly.
