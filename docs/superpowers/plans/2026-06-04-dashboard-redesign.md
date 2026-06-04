# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle `dashboard.html` to a Modern Fintech look and give every metric a single home, removing 11 duplicated readouts, without touching any data wiring or bot logic.

**Architecture:** Single self-contained file (`dashboard.html` = HTML + `<style>` + `<script>`) served by `web_app.py`. Changes are presentational: edit the `:root` tokens + component CSS, prune duplicate DOM nodes, and delete the exact `<script>` updater lines that targeted the removed nodes (so `$(id)` never returns `null`). Relocate the existing `pixelAgents` canvas into a center placeholder card reserved for sub-project #2.

**Tech Stack:** Vanilla HTML/CSS/JS. No build step. Manual browser verification (no front-end test harness). Flask dev server: `python web_app.py` → `http://localhost:8080`.

**Spec:** `docs/superpowers/specs/2026-06-04-dashboard-redesign-design.md`

---

## File Structure

- Modify only: `dashboard.html`
  - `<style>` block (`:root` tokens + component classes) — visual system
  - body markup — IA cleanup (remove duplicate nodes, relocate Trading Floor)
  - `<script>` `fetchAndRender()` — delete updater lines for removed nodes
- Do NOT touch: `web_app.py`, `pixel_agents.js`, `/api/*`, `system_utils.py`, bot code.

## Hard Constraint (repeat — applies to every task)

For each removed DOM node, delete its `$('<id>').…` updater line(s) in the **same** task. Keep the canonical node's ID unchanged so its updater keeps working. Never change `/api` response usage or any computed value — only delete *assignment to removed nodes*. Some computed locals (e.g. `todayPnl`, `todayW`, `todayL`) feed surviving KPIs — keep the computation, delete only the topbar assignment.

---

### Task 0: Branch + baseline

**Files:** none (git only)

- [ ] **Step 1: Create a feature branch**

```bash
cd "PC Trading 2.17"
git checkout -b dashboard-redesign
```

- [ ] **Step 2: Baseline the current dashboard**

Run: `python web_app.py` then open `http://localhost:8080`.
Open DevTools Console. Confirm the dashboard renders and the console has **no errors** (this is the baseline — every later task must keep it error-free). Stop the server (Ctrl+C) when done.

- [ ] **Step 3: Commit the branch point (no-op marker)**

```bash
git commit --allow-empty -m "chore: start dashboard redesign branch"
```

---

### Task 1: Modern Fintech design tokens + base cards

Update the visual system. Keep all `:root` variable **names** (existing classes reference them); change values only. Add card-depth + radius treatments.

**Files:**
- Modify: `dashboard.html` `<style>` — line 9 (`:root`) and the `.panel`/card base rules.

- [ ] **Step 1: Replace the `:root` token line**

Find line 9 (`:root{--bg:#0a0c0f; … }`) and replace the whole `:root{…}` block with:

```css
:root{
  --bg:#0d1117;--bg2:#0f1524;--bg3:#151c2c;--bg4:#1b2336;
  --border:#232b3d;--border2:#2a3a52;
  --text:#d5deec;--text2:#94a3b8;--text3:#64748b;
  --accent:#7aa2ff;--accent2:#5e8bff;
  --green:#4ade80;--red:#fb7185;--yellow:#fbbf24;--purple:#a855f7;--orange:#ff6b35;--pink:#ff4fa3;
  --r2:#fb7185;  /* alias kept: JS sets className 'r2' for losses */
  --font-mono:'JetBrains Mono',monospace;--font-ui:'Inter',sans-serif;
  --card-shadow:0 2px 8px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.05);
}
```

Note: the JS sets `className` strings containing `r2`, `g`, `y`, `b`, `a`. Verify those color classes still exist further down the stylesheet (`.val.r2`, `.val.g`, etc.). If `.r2`/`.val.r2` is not already defined, add `.val.r2{color:var(--red)}` and `.r2{color:var(--red)}` next to the existing `.val.r{…}` rule (around line 19).

- [ ] **Step 2: Soften cards and panels**

Find the `.panel` rule (around line 99: `.panel{background:var(--bg2);…}`) and the equity/risk/agent card wrappers. Apply rounded corners + depth. Replace the `.panel` rule with:

```css
.panel{background:var(--bg3);display:flex;flex-direction:column;overflow:hidden;border-radius:12px;box-shadow:var(--card-shadow)}
```

Find `.grid4` (around line 98: `.grid4{… gap:1px; background:var(--border)}`) and replace with:

```css
.grid4{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:9px;flex:1;overflow:hidden;background:transparent;padding:9px}
```

- [ ] **Step 3: Verify render**

Run: `python web_app.py` → reload `http://localhost:8080`.
Expected: same content, now bluer background, rounded cards with subtle shadow, gaps between panels. Console: **no errors**. Stop server.

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "style: Modern Fintech design tokens + card depth"
```

---

### Task 2: Topbar declutter (identity + status only)

Remove the 9 money/status numbers + regime pills + conn-dot from the topbar markup, and delete their exact updater lines. Keep `tb-scan`, `clock`, logo, mode badge.

**Files:**
- Modify: `dashboard.html` markup lines 312–322 (topbar stats) and `<script>` lines 936–937, 942–950, 953–962, 969–972, 1015–1018, 1055–1059, 1084.

- [ ] **Step 1: Trim the topbar markup**

In the `<!-- TOPBAR -->` block (lines 310–330), delete these lines (the duplicated readouts):
- line 312 `tb-balance`, 313 `tb-equity`, 314 `tb-float`, 315 `tb-today`, 316 `tb-dd`, 317 `tb-open`, 318 `tb-total`, 319 `tb-wr`, 321 `tb-ai`
- line 322 `regime-pills-wrap`
- line 324 `conn-dot` (the `<div class="conn-dot" id="conn-dot">`)

Keep: logo (311), `tb-scan` (320), mode badges (325–327), clock (328).
Add a status chip after the logo for DIRECTOR direction (purely presentational, fed by existing data in a later optional step — for now static text is fine):

```html
<div class="tb-stat"><span class="lbl">Status</span><span class="val a" id="tb-scan">—</span></div>
```
(Leave `tb-scan` exactly as-is; do not rename. The line above is illustrative of the kept element — do not duplicate it.)

- [ ] **Step 2: Delete the topbar updater lines in `fetchAndRender()`**

Delete these exact lines:
- 936–937 (`const dot = $('conn-dot'); dot.classList.toggle('online', !!d.state);`)
- 942–950 (`$('tb-balance')…` through `$('tb-open')…`) — **but keep 951 `$('tb-scan')…`**
- 953–962 (regime pills `const pillsWrap = $('regime-pills-wrap'); … }`)
- 969–972 (the `if($('tb-today')){…}` block) — **keep 964–968 (todayPnl/W/L computation; used by KPIs)**
- 1015–1018 (`$('tb-total')…`, `$('tb-wr')…` topbar totals) — **keep 1003–1013 (s-total/s-winrate KPIs)**
- 1055–1059 (`$('tb-ai')…` AI status block)

- [ ] **Step 3: Fix the catch block (conn-dot removed)**

Line 1084 references the removed node. Replace line 1084:

```js
    $('conn-dot').classList.remove('online');
```
with (delete it — keep the hb-txt error on the next line):
```js
    // connection state shown via control-bar pills (updateCtrlBar)
```

- [ ] **Step 4: Verify no dangling references**

Run: `grep -nE "tb-balance|tb-equity|tb-float|tb-today|tb-dd|tb-open|tb-total|tb-wr|tb-ai|regime-pills-wrap|conn-dot" dashboard.html`
Expected: **no matches** (all nodes and updaters gone). If any remain, remove them.

- [ ] **Step 5: Verify render**

Run: `python web_app.py` → reload. Expected: topbar shows logo + Scan# + mode + clock only; **no console errors**; all numbers still appear once in sidebar/KPIs. Stop server.

- [ ] **Step 6: Commit**

```bash
git add dashboard.html
git commit -m "refactor(dashboard): topbar = identity + status, drop 11 dup readouts"
```

---

### Task 3: Left column — Account / Risk+DD+Regime / Calendar / Agents

Restyle the left sidebar into discrete Modern-Fintech cards and fold the regime strip into the Risk & Drawdown card (regime's single home). No JS changes here except none required — all these IDs (`eq-val, eq-chg, risk-val, risk-fill, dd-val, dd-max, dd-fill, r-*, cal-*, ag*-*`) remain.

**Files:**
- Modify: `dashboard.html` markup lines 357–421 (left sidebar) and related `<style>` for `.sidebar`, `.eq-wrap`, `.risk-wrap`, `.dd-wrap`, `.reg-bar`, `.sec-title`, `.agent-row`.

- [ ] **Step 1: Wrap sidebar sections in cards**

In the `.sidebar` block, group the markup into `.card`-styled wrappers: (a) Account = `eq-wrap` (lines 360–364), (b) Risk & Drawdown = `risk-wrap` + `dd-wrap` + `reg-bar` (lines 366–383) combined into one card, (c) Economic Calendar (385–386), (d) Agents (388–420). Add this CSS near the sidebar styles:

```css
.sidebar{background:var(--bg);gap:9px;padding:9px}
.sidebar .card{background:var(--bg3);border-radius:12px;box-shadow:var(--card-shadow);padding:10px;margin:0}
.sidebar .sec-title{background:transparent;border:none;padding:0 0 6px;color:var(--text3)}
.reg-bar{background:transparent;border:none;padding:6px 0 0}
.reg-seg{border-radius:6px}
.agent-row{border-radius:9px}
```

- [ ] **Step 2: Verify render**

Run: `python web_app.py` → reload. Expected: left column shows 4 rounded cards (Account, Risk & Drawdown incl. regime strip, Calendar, Agents); equity/risk/DD/regime each appear once here; **no console errors**. Stop server.

- [ ] **Step 3: Commit**

```bash
git add dashboard.html
git commit -m "style(dashboard): left column cards + regime folded into Risk/DD"
```

---

### Task 4: Center column — KPI row, panels, Trading Floor placeholder

Restyle the 6 KPIs and 4 panels; remove the duplicate `s-risk` KPI (risk lives in the left gauge); relocate the existing `pixelAgents` canvas into a flex-fill placeholder card in the center column (reserved for sub-project #2).

**Files:**
- Modify: `dashboard.html` markup lines 426–433 (stats row), 441–602 (grid4 panels), 612–619 (right-sidebar Trading Floor block to relocate); `<script>` line 1000 (`s-risk`).

- [ ] **Step 1: Remove the `s-risk` KPI node + updater**

Markup: delete line 432 (`<div class="sc">…id="s-risk"…id="s-maxdd"…</div>`) and re-add Max DD as its own KPI so MaxDD survives:

```html
<div class="sc"><span class="lbl">Max DD</span><span class="val r2" id="s-maxdd">—</span><span class="sub">peak</span></div>
```
Script: delete line 1000 (`$('s-risk').textContent = `${risk}/5`;`). Keep line 1001 (`$('s-maxdd')…`).

Update `.stats-row` to 6 rounded KPI tiles:

```css
.stats-row{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;background:transparent;border:none;padding:9px 9px 0;flex-shrink:0}
.sc{background:var(--bg3);border-radius:10px;box-shadow:var(--card-shadow);padding:9px 11px}
```

- [ ] **Step 2: Relocate the Trading Floor canvas into the center**

Cut the Trading Floor markup from the right sidebar (lines ~612–619: the `sec-title "Trading Floor"` + the `<canvas id="pixelAgents">` wrapper) and paste it as a new flex-fill card at the **end of the `.main` column**, after the `</div>` that closes `.grid4` (line 602–603). Wrap it:

```html
<!-- Trading Floor placeholder (sub-project #2 will render here) -->
<div class="panel floor-placeholder" style="flex:1;min-height:150px;margin:9px">
  <div class="ph"><div class="ph-title"><div class="pdot g"></div>Trading Floor</div><span style="font-size:9px;color:var(--text3)">live agents</span></div>
  <div class="pb" style="padding:6px 8px"><canvas id="pixelAgents" style="width:100%;display:block;image-rendering:pixelated"></canvas></div>
</div>
```

`initPixelAgents('pixelAgents')` (line 1151) targets by ID, so the canvas keeps working in its new location — no JS change needed.

- [ ] **Step 3: Restyle the 4 grid panels' headers**

Find `.ph` (panel header, around line 100) and round it:

```css
.ph{display:flex;align-items:center;justify-content:space-between;padding:8px 11px;border-bottom:1px solid var(--border);flex-shrink:0;background:transparent}
```

- [ ] **Step 4: Verify render**

Run: `python web_app.py` → reload. Expected: 6 KPI tiles (Risk tile gone, Max DD present once), 4 rounded panels, and the Trading Floor canvas now in the center below the panels; right sidebar no longer shows Trading Floor; **no console errors**; `grep -n "id=\"pixelAgents\"" dashboard.html` returns exactly one match. Stop server.

- [ ] **Step 5: Commit**

```bash
git add dashboard.html
git commit -m "refactor(dashboard): center KPIs + relocate Trading Floor to center placeholder"
```

---

### Task 5: Right column — single-home Symbols + Macro restyle

Remove the duplicate Symbols cards from the right sidebar (center `Symbol Monitor` / `sym-cards-inner` is the single home) and make `renderSymbols` target only the surviving container.

**Files:**
- Modify: `dashboard.html` markup lines 606–611 (right sidebar Symbols + Macro) and the `renderSymbols(...)` function (search for `function renderSymbols`).

- [ ] **Step 1: Inspect `renderSymbols`**

Run: `grep -n "function renderSymbols" dashboard.html` then read it. Note which container ID(s) it writes to (`sym-cards` and/or `sym-cards-inner`).

- [ ] **Step 2: Remove the right-sidebar Symbols node**

Delete markup lines 607–608 (`<div class="sec-title">Symbols</div>` + `<div class="sym-cards" id="sym-cards">`). Keep the Macro Data section (610–611).

In `renderSymbols`, delete any write to `#sym-cards`; keep only the write to `#sym-cards-inner` (the center Symbol Monitor). If `renderSymbols` only wrote to `#sym-cards`, repoint it to `#sym-cards-inner`.

- [ ] **Step 3: Restyle Macro + right sidebar as cards**

```css
.rsidebar{background:var(--bg);gap:9px;padding:9px}
.rsidebar .sec-title{background:transparent;border:none;padding:0 0 6px;color:var(--text3)}
#macro-list{background:var(--bg3);border-radius:12px;box-shadow:var(--card-shadow);padding:10px}
```

- [ ] **Step 4: Verify no dangling Symbols reference**

Run: `grep -nE "sym-cards\b|id=\"sym-cards\"" dashboard.html`
Expected: no matches for the removed `id="sym-cards"`; `sym-cards-inner` still present once. Reload `http://localhost:8080`: symbols appear only in the center Symbol Monitor; **no console errors**. Stop server.

- [ ] **Step 5: Commit**

```bash
git add dashboard.html
git commit -m "refactor(dashboard): single-home Symbols (center), restyle right column"
```

---

### Task 6: Responsive pass + final verification

**Files:**
- Modify: `dashboard.html` `@media` blocks (search `@media`).

- [ ] **Step 1: Check the breakpoints**

Run: `python web_app.py` → at `http://localhost:8080`, use DevTools device toolbar to test widths 1280 / 900 / 480. The 3-column `.layout` (line 33) should stack at ≤900px. Confirm the new center Trading Floor placeholder and KPI grid collapse without overflow.

- [ ] **Step 2: Fix any overflow in the small breakpoints**

If the 6-KPI row overflows at 480px, add to the existing `@media (max-width:480px)` block:

```css
.stats-row{grid-template-columns:repeat(2,1fr)}
.floor-placeholder{min-height:120px}
```

- [ ] **Step 3: Full verification checklist (manual)**

Run: `python web_app.py` → `http://localhost:8080`. Confirm ALL:
- Console: **no errors** across a few 5s refresh cycles.
- Each metric appears exactly once: Equity (left), Risk/DD/Regime (left), Today P&L / Float / Win Rate / Total / Max DD / Bot State (center KPIs), Symbols (center), Macro (right).
- Control bar START/STOP/PAUSE/RESTART still POSTs `/api/control`; Risk 1–5 buttons still highlight + set.
- Trading Floor canvas animates in the center placeholder.
- Stop server.

- [ ] **Step 4: Final dup scan**

Run: `grep -nE "tb-balance|tb-equity|tb-float|tb-today|tb-dd|tb-open|tb-total|tb-wr|tb-ai|regime-pills-wrap|conn-dot|s-risk|id=\"sym-cards\"" dashboard.html`
Expected: **zero matches**.

- [ ] **Step 5: Commit + finish branch**

```bash
git add dashboard.html
git commit -m "style(dashboard): responsive pass + final dedup verification"
```

Then use the `superpowers:finishing-a-development-branch` skill to decide merge/PR. Deploy (push → server `git pull` → `deploy.flag`) only after the user approves, per `CLAUDE.md`. Note: this is a front-end-only change; the bot restart on `deploy.flag` will reload it.

---

## Self-Review

**Spec coverage:**
- Modern Fintech tokens → Task 1 ✓
- Topbar identity-only, remove 9 numbers + regime pills + conn-dot → Task 2 ✓
- Left column IA (equity/risk/dd/regime single home) → Task 3 ✓
- Center KPIs, remove s-risk dup, Trading Floor placeholder → Task 4 ✓
- Right column single-home Symbols + Macro → Task 5 ✓
- Risk 3-homes→2 roles (control setter + sidebar gauge; remove s-risk) → Task 4 ✓
- Keep-wiring constraint (delete updater per removed node) → every task ✓
- Responsive → Task 6 ✓
- Out of scope (api/bot/virtual-office engine) → untouched ✓

**Placeholder scan:** No TBD/TODO. Trading Floor "placeholder" is an intentional reserved region (sub-project #2), not a plan gap.

**Type/ID consistency:** Removed IDs (`tb-*`, `regime-pills-wrap`, `conn-dot`, `s-risk`, `sym-cards`) are deleted as node+updater pairs; surviving IDs (`tb-scan`, `eq-val`, `eq-chg`, `risk-val`, `risk-fill`, `dd-*`, `s-today-pnl`, `s-float`, `s-winrate`, `s-total-trades`, `s-maxdd`, `s-state`, `sym-cards-inner`, `pixelAgents`) keep their names and updaters. `--r2` alias added so JS `className 'r2'` still resolves.

**Note on line numbers:** line references are from the current `dashboard.html`. After each task the file shifts — locate by surrounding code/ID, not absolute line number, when a reference drifts.
