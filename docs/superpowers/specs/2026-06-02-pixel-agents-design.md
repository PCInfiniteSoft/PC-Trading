# Pixel Agents — Design Spec
**Date:** 2026-06-02
**Status:** Approved

## Summary

Add a pixel-art trading floor to the right sidebar of `dashboard.html`. Six animated characters represent the bot's AI agents in real-time, each reacting to live state from `/api/status`. Implemented with HTML5 Canvas + vanilla JS — no new dependencies.

---

## Characters

| Character | Symbol | Color | Role |
|---|---|---|---|
| DIRECTOR | both | green `#00e676` | macro strategist, 1 instance |
| ANALYST_BTC | BTCUSDm | blue `#0099ff` | entry scoring |
| ANALYST_XAU | XAUUSDm | gold `#ffc107` | entry scoring |
| SCOUT_BTC | BTCUSDm | cyan `#00d4aa` | MACD pre-filter |
| SCOUT_XAU | XAUUSDm | orange `#ff6b35` | MACD pre-filter |
| GUARDIAN | both | red `#ff3d57` | risk gate, 1 shared instance |

---

## Layout

**Placement:** Right sidebar (220px wide), replaces existing `.ag-panel-wrap` agent card section.

**Structure (top to bottom):**
1. Sidebar header bar — `🟢 TRADING FLOOR` label
2. Symbol price cards — BTC / XAU (existing, keep)
3. `<canvas id="pixelAgents" width="200" height="310">` — the pixel scene

**Canvas zones (vertical stacking):**
```
┌──────────────────────────┐  y=0
│  DIRECTOR                │  h=82px
├──────────────────────────┤  y=82
│  BTC zone (blue border)  │  h=110px
│    ANALYST_BTC  SCOUT_BTC│
├──────────────────────────┤  y=192
│  XAU zone (gold border)  │  h=65px
│    ANALYST_XAU  SCOUT_XAU│
├──────────────────────────┤  y=257
│  GUARDIAN                │  h=53px
└──────────────────────────┘  y=310
```

---

## Sprite System

- Each sprite: 8px wide × 20px tall (head 8×8 + body 8×6 + legs 8×4 + feet 8×2)
- Render scale: **3×** → 24px wide × 60px tall on canvas
- Sprites are drawn programmatically with `ctx.fillRect` — no image assets required
- Eye pixels hardcoded black at head rows 3–4, cols 2 and 5

---

## Animation States

### DIRECTOR

| State | Trigger | Animation | Bubble |
|---|---|---|---|
| BULLISH | `bias = BULLISH \| STRONG_BULLISH` | idle, bright green glow | `📈 BUY_ONLY` |
| BEARISH | `bias = BEARISH \| STRONG_BEARISH` | alert pulse, red tint | `📉 SELL_ONLY` |
| SIDEWAY | `bias = SIDEWAY` | idle, dim | `↔ BOTH` |
| NEWS_PAUSE | `allowed_direction = NONE` | alert pulse, yellow | `⚠️ News pause` |

### ANALYST_BTC / ANALYST_XAU

| State | Trigger | Animation | Bubble |
|---|---|---|---|
| IDLE | `bot_state = WAITING` | slow bob | `รอ RSI...` |
| SCORING | `analyst_busy = true` | head tilt (think) | `Computing...` |
| SIGNAL_BUY | `last_decision = BUY` | celebrate (jump) 3s → IDLE | `🟢 BUY! X/15` |
| SIGNAL_SELL | `last_decision = SELL` | celebrate (jump) 3s → IDLE | `🔴 SELL! X/15` |
| HOLD | `last_decision = HOLD` | dim (opacity 0.35) | `X/15 — HOLD` |

When a BTC trade fires, ANALYST_XAU dims (opacity 0.35) — not relevant to that event.

### SCOUT_BTC / SCOUT_XAU

| State | Trigger | Animation | Bubble |
|---|---|---|---|
| BULLISH | `strategy_data[sym].regime` = `TRENDING_UP\|PULLBACK` | walk cycle, full color | `MACD ✅` |
| BEARISH | `strategy_data[sym].regime` = `TRENDING_DOWN\|VOLATILE` | walk cycle, desaturated | `MACD ⚠️` |
| NEUTRAL | `regime = RANGING` | slow walk, mid color | `Ranging` |

### GUARDIAN

| State | Trigger | Animation | Bubble |
|---|---|---|---|
| CLEAR | `guardian_blocked = false` | idle | `🛡 All OK` |
| BLOCKING | `guardian_blocked = true` | shake + red flash | `🛑 [reason]` |
| COOLDOWN | `cooldown_remaining > 0` | dim pulse orange | `⏳ Xm left` |

---

## Data Flow

```
shared_state / bot_status.json
       ↓
/api/status  (poll every 3s, existing dashboard interval)
       ↓
StateMapper (pixel_agents.js)
  → parses JSON → AgentState[] (one per character)
       ↓
AnimationController
  → manages frame counters, transition timers (e.g. celebrate → idle after 3s)
       ↓
Canvas render loop (requestAnimationFrame, 60fps)
  → drawChar(), drawBubble(), drawZoneBox()
  → dirty-flag: only redraw zones where state changed
```

---

## Files Changed

### New: `pixel_agents.js`

Self-contained module, ~300 LOC. Exports one function: `initPixelAgents(canvasId, apiUrl)`.

Sections:
1. `SPRITE_DEFS` — color palettes per character
2. `StateMapper` — maps `/api/status` JSON → `AgentState[]`
3. `AnimationController` — per-agent frame counter, transition timer
4. `Renderer` — `drawChar`, `drawBubble`, `drawZoneBox`, `render()`
5. Poll loop — `setInterval(fetchAndUpdate, 3000)`

### Modified: `dashboard.html`

- Replace `.ag-panel-wrap` div in right sidebar with `<canvas id="pixelAgents" width="200" height="310"></canvas>`
- Add `<script src="pixel_agents.js"></script>` before `</body>`
- Call `initPixelAgents('pixelAgents', '/api/status')` in existing `DOMContentLoaded`

### Modified: `web_app.py`

Add two fields to `/api/status` response in `api_status()`:

```python
data["guardian_blocked"] = getattr(_shared_state, 'GUARDIAN_BLOCKED', False)
data["guardian_block_reason"] = getattr(_shared_state, 'GUARDIAN_BLOCK_REASON', '')
data["analyst_busy"] = getattr(_shared_state, 'ANALYST_BUSY', {})
```

Also expose existing `ai_engine.STRATEGY_DATA` (already populated, just not yet included in status):

```python
if _ai_engine is not None:
    data["strategy_data"] = {
        sym: {
            "regime": v.get("regime", "N/A"),
            "buy": v.get("buy", []),
            "sell": v.get("sell", []),
        }
        for sym, v in _ai_engine.STRATEGY_DATA.items()
    }
```

And set these in the bot loop:
- `shared_state.GUARDIAN_BLOCKED` = True briefly when any GUARDIAN gate fires
- `shared_state.ANALYST_BUSY[symbol]` = True while ANALYST API call is in-flight

---

## Error Handling

- If `/api/status` fetch fails → keep last known state, no visual change
- If `macro_data` key missing for a symbol → DIRECTOR shows SIDEWAY (safe default)
- Canvas not supported → hide canvas, show static text fallback

---

## Out of Scope

- Sound effects
- Click interactions on characters
- Tilemap / room background (plain dark fill only)
- Mobile responsiveness
