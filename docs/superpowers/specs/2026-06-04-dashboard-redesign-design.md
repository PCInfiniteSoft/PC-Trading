# Dashboard Redesign — Modern Fintech + Declutter

**Date:** 2026-06-04
**Status:** Implemented on branch `dashboard-redesign` (unmerged) — see Progress section
**File touched:** `dashboard.html` (single file: HTML + `<style>` + `<script>`)

## Problem

The live web dashboard (`dashboard.html`, ~1155 lines, served by `web_app.py`)
works but the user finds it visually dated and **cluttered with duplicated
data** — the same metric is rendered in 2–3 places. Confirmed duplicates:

| Metric | Rendered in | Times |
|--------|-------------|-------|
| Equity | topbar `tb-equity` + sidebar `eq-val` | 2 |
| Float P&L | topbar `tb-float` + stats `s-float` | 2 |
| Today P&L | topbar `tb-today` + stats `s-today-pnl` | 2 |
| Drawdown | topbar `tb-dd` + sidebar `dd-val` + stats `s-maxdd` | 3 |
| Open count | topbar `tb-open` + stats `s-open` + `pos-count` | 3 |
| Total Trades | topbar `tb-total` + stats `s-total-trades` | 2 |
| Win Rate | topbar `tb-wr` + stats `s-winrate` | 2 |
| Risk Level | control `.rb` + sidebar `risk-val` + stats `s-risk` | 3 |
| Regime | topbar `regime-pills-wrap` + sidebar `reg-bar` | 2 |
| Symbols | center `sym-cards-inner` + right sidebar `sym-cards` | 2 |
| AI / Connection status | topbar `tb-ai`/`conn-dot` + control conn-pills | 2 |

## Goal

Restyle to a **Modern Fintech** aesthetic (option B: softer dark, card depth,
rounded corners, subtle gradients, more whitespace) **and** give every metric a
single home. Carve out a placeholder region for the future Virtual Office
(sub-project #2).

## Scope

**In scope** — purely presentational changes to `dashboard.html`:
- New CSS design tokens + restyled components (cards, topbar, panels, KPIs).
- Information-architecture cleanup: remove duplicate DOM nodes; each metric has
  one canonical element ID.
- Layout: 3-column grid retained; Trading Floor moves from the right sidebar to
  a flex-fill card in the **center column** (the empty gap below the panels),
  rendered as a styled placeholder box in #1.

**Out of scope** (do NOT touch):
- `web_app.py`, any `/api/*` endpoint, `system_utils.py`, or bot logic.
- The Virtual Office canvas engine + assets → **sub-project #2**, its own spec.
- Data semantics. We only move/merge *where* a value is shown, never compute it.

## Hard Constraint — keep data wiring intact

The `<script>` updates the DOM via `$ = id => document.getElementById(id)` then
`$(id).textContent = …`. If a referenced element is deleted, `$()` returns
`null` and the assignment throws, breaking the whole render loop.

**Rule:** for every duplicate DOM node removed, its JS updater line must be
removed in the same change. The surviving (canonical) element keeps its existing
ID so its updater keeps working untouched. No `/api` response shape changes.

## Information Architecture — one home per metric

Each column owns a category. Canonical home in **bold**; the ID kept is noted.

- **Topbar = identity + global status only** (no money numbers): logo, market
  badge, `DIRECTOR · <allowed_direction>`, mode badge (`DEMO/LIVE/PAPER`),
  `Scan #` (`tb-scan`), clock (`clock`).
  Removed from topbar: `tb-balance, tb-equity, tb-float, tb-today, tb-dd,
  tb-open, tb-total, tb-wr, tb-ai, conn-dot` (+ their updater lines).
  `regime-pills-wrap` removed (regime lives in sidebar). Connection status
  (Discord/MT5/AI) has its single home in the **control-bar conn-pills**
  (`pill-discord/mt5/ai`), which already exist.
- **Left column = account & context:**
  - Account card → Equity (`eq-val`), change (`eq-chg`), balance as sub-line, curve canvas (`eqCanvas`).
  - Risk & Drawdown card → Risk readout (`risk-val` + `risk-fill`), Daily DD (`dd-val`, `dd-max`, `dd-fill`), Regime strip (`reg-bar` / `r-*`).
  - Economic Calendar (`cal-list`, `cal-cnt`).
  - Agents compact status list (existing `ag*-sub/badge`, `mt5-*`, `loop-*`).
- **Center column = performance + work area:**
  - KPI row (dedup target): Bot State (`s-state`), Today P&L (`s-today-pnl`),
    Float P&L (`s-float`), Win Rate (`s-winrate`), Total Trades
    (`s-total-trades`), Max DD (`s-maxdd`).
  - Heartbeat (`hb-txt`, `hb-time`).
  - Panels: Open Positions (`pos-table`, `pos-count`), Strategy Performance 7d
    (`strat-table`), Tabs [Recent Trades `trades-table` / System Logs
    `logs-list` / Agents `tab-agents`], Symbol Monitor (`sym-cards-inner`).
  - **Trading Floor placeholder card** (flex-fill) — styled empty box reserved
    for sub-project #2 canvas.
- **Right column = market context:** Macro Data (`macro-list`). Remove duplicate
  right-sidebar Symbols (`sym-cards`) + its updater — Symbol Monitor in center
  is the single home. Right column also hosts a "Last Guardian Block" readout
  (uses existing log/agent data; no new API).

### Risk Level (3 homes → 2 distinct roles)
- Control bar `.rb` buttons stay (interactive setter — `setRisk`).
- Sidebar `risk-val`/`risk-fill` stays (single passive readout).
- Stats `s-risk` removed (+ its updater) — redundant with the sidebar gauge.

### Agents (note, resolved in #2)
Sidebar compact list stays. The detailed **Agents tab** (`tab-agents`) is kept
as-is in #1; it will be re-evaluated in sub-project #2 once the Virtual Office
becomes the primary agent visualization.

## Visual System — Modern Fintech tokens

Evolve the existing `:root` variables, keep the same variable *names* so existing
class usage keeps working; only values/treatments change.
- Background layers slightly bluer/softer; cards `#151c2c` with
  `box-shadow: 0 2px 8px rgba(0,0,0,.3), inset 0 1px 0 rgba(255,255,255,.05)`.
- Radius: cards `11–12px`, pills/rows `8–10px` (was `2–3px`).
- Accent shifts toward fintech blue `#7aa2ff` for structure, green `#4ade80` /
  red `#fb7185` for P&L, amber `#fbbf24` for risk.
- Keep `--font-mono` (JetBrains Mono) for numbers, `--font-ui` (Inter) for labels.
- More padding/whitespace; gradient topbar; subtle inset highlights.
- Equity/strategy/symbol value formatting unchanged (driven by JS).

## Responsive

Preserve the existing `@media` breakpoints (900px / 480px stack behaviour).
Verify the new card grid and the center Trading Floor placeholder collapse
cleanly; the placeholder may hide below the small breakpoint.

## Testing / Verification

No unit-test harness for the front-end. Verification is manual via `verify`/`run`:
1. `python web_app.py` → open `http://localhost:8080`.
2. Confirm the render loop runs with **no console errors** (proves no dangling
   `$()` after node removal).
3. Confirm every metric appears exactly once and updates live.
4. Confirm control bar (START/STOP/PAUSE/RESTART, Risk 1–5) still posts to
   `/api/control` and reflects state.
5. Confirm responsive stack at 900px / 480px.

## Follow-up

**Sub-project #2 — Reactive Virtual Office:** pixel-art top-down office rendered
on canvas in the center placeholder; characters = agents with live
busy/idle/online status and event reactions, fed by `/api/status` + a log/event
feed. Asset pack (e.g. LimeZu Modern Office, itch.io) acquired by the user under
appropriate license. Separate spec.

## Progress (as of 2026-06-04)

**DONE** — implemented on branch `dashboard-redesign` (off main, 8 commits, head `c33a386e`), NOT merged. Executed via subagent-driven development (plan `docs/superpowers/plans/2026-06-04-dashboard-redesign.md`), all 6 tasks + 2 review follow-ups:
- Modern Fintech design tokens + card depth
- Topbar decluttered to identity+status (removed 11 duplicate readouts + their JS updaters)
- Left column cards; regime folded into Risk/DD card
- Center KPIs; removed `s-risk` duplicate; relocated `pixelAgents` canvas to center placeholder
- Right column single-home Symbols; Macro restyle
- Responsive pass; relabel Drawdown KPI; mobile KPI `!important` fix
- Verified STATIC only (div balance proven, zero dangling JS refs, scope clean, CSS valid) + opus final review = APPROVED_WITH_FOLLOWUPS. Repo tests 10/10.

**PENDING:**
- Live browser verification — run `web_app.py` → `localhost:8080`, confirm no console errors + each metric renders once (not done; no browser in dev env).
- Merge branch → main, then deploy (push → server `git pull` → `deploy.flag`) per `CLAUDE.md`.
- Cosmetic follow-ups (non-blocking): ~20 old `rgba()` accent tints (badges/regime) don't match new `--green`/`--red` hex; dead CSS `.regime-wrap` / `.sym-cards`.
- Sub-project #2 (Reactive Virtual Office) not started — needs its own spec.
