# Pixel Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an animated pixel-art trading floor to the right sidebar of `dashboard.html`, showing 6 characters (DIRECTOR, ANALYST_BTC, ANALYST_XAU, SCOUT_BTC, SCOUT_XAU, GUARDIAN) that animate based on live bot state from `/api/status`.

**Architecture:** A new self-contained JS module (`pixel_agents.js`) draws characters onto a `<canvas>` element in the right sidebar. It polls `/api/status` every 5s (matching the dashboard's existing interval) and drives a per-agent state machine. Backend adds three new fields to `/api/status` by reading from `shared_state` and `ai_engine.STRATEGY_DATA`.

**Tech Stack:** Vanilla JS, HTML5 Canvas API, Python Flask (existing), no new dependencies.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `shared_state.py` | Modify | Add `GUARDIAN_BLOCKED`, `GUARDIAN_BLOCK_REASON`, `ANALYST_BUSY` globals |
| `web_app.py` | Modify | Expose `guardian_blocked`, `analyst_busy`, `strategy_data` in `/api/status` |
| `risk_manager.py` | Modify | Set `shared_state.GUARDIAN_BLOCKED` when any gate fires |
| `ai_engine.py` | Modify | Set `shared_state.ANALYST_BUSY[symbol]` around ANALYST API call |
| `pixel_agents.js` | Create | Sprite renderer, StateMapper, AnimationController, poll loop |
| `dashboard.html` | Modify | Add `<canvas>` to right sidebar, `<script>` tag, call `initPixelAgents()` |

---

## Task 1: Add state variables to `shared_state.py`

**Files:**
- Modify: `shared_state.py`

- [ ] **Step 1: Add three globals at the end of `shared_state.py`**

Open `shared_state.py` and append:

```python
# Pixel Agents — live state for dashboard canvas
GUARDIAN_BLOCKED = False          # True briefly when any GUARDIAN gate fires
GUARDIAN_BLOCK_REASON = ""        # e.g. "Spread too high", "Dead hour"
ANALYST_BUSY = {}                 # { "BTCUSDm": True/False, "XAUUSDm": True/False }
```

- [ ] **Step 2: Verify by running Python**

```bash
cd "C:/Users/PC-Laptop/Documents/Dev Project/PC Trading/PC Trading 2.17"
python -c "import shared_state; print(shared_state.GUARDIAN_BLOCKED, shared_state.ANALYST_BUSY)"
```

Expected output:
```
False {}
```

- [ ] **Step 3: Commit**

```bash
git add shared_state.py
git commit -m "feat: add GUARDIAN_BLOCKED and ANALYST_BUSY to shared_state"
```

---

## Task 2: Expose new fields in `/api/status`

**Files:**
- Modify: `web_app.py` (lines ~72–84, inside `api_status()`)

- [ ] **Step 1: Add new fields to `api_status()` in `web_app.py`**

Find this block in `api_status()`:
```python
    if _ai_engine is not None:
        data["ai_online"] = _ai_engine.AI_IS_ONLINE
        data["ai_error_code"] = _ai_engine.AI_ERROR_CODE
    with _log_lock:
        data["logs"] = list(reversed(_log_buffer[-50:]))
    return jsonify(data)
```

Replace with:
```python
    if _ai_engine is not None:
        data["ai_online"] = _ai_engine.AI_IS_ONLINE
        data["ai_error_code"] = _ai_engine.AI_ERROR_CODE
        data["strategy_data"] = {
            sym: {
                "regime": v.get("regime", "N/A"),
                "buy": v.get("buy", []),
                "sell": v.get("sell", []),
                "atr_pct": v.get("atr_pct", 0.0),
            }
            for sym, v in _ai_engine.STRATEGY_DATA.items()
        }
    if _shared_state is not None:
        data["guardian_blocked"] = getattr(_shared_state, "GUARDIAN_BLOCKED", False)
        data["guardian_block_reason"] = getattr(_shared_state, "GUARDIAN_BLOCK_REASON", "")
        data["analyst_busy"] = getattr(_shared_state, "ANALYST_BUSY", {})
    with _log_lock:
        data["logs"] = list(reversed(_log_buffer[-50:]))
    return jsonify(data)
```

- [ ] **Step 2: Start web_app standalone and verify new fields**

In one terminal:
```bash
cd "C:/Users/PC-Laptop/Documents/Dev Project/PC Trading/PC Trading 2.17"
python web_app.py
```

In another terminal:
```bash
python -c "import requests,json; d=requests.get('http://localhost:8080/api/status').json(); print(list(d.keys()))"
```

Expected output includes: `'strategy_data'`, `'guardian_blocked'`, `'analyst_busy'`

Kill the test server with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add web_app.py
git commit -m "feat: expose strategy_data, guardian_blocked, analyst_busy in /api/status"
```

---

## Task 3: Wire `GUARDIAN_BLOCKED` in `risk_manager.py`

**Files:**
- Modify: `risk_manager.py`

The goal: when any gate method returns `True` (blocked), set `shared_state.GUARDIAN_BLOCKED = True` with a reason. When it returns `False`, reset. We do this inside the existing gate methods, not in a caller.

- [ ] **Step 1: Add a helper method to `RiskManager`**

Inside the `RiskManager` class in `risk_manager.py`, add this helper right after `__init__`:

```python
    @staticmethod
    def _set_blocked(reason: str):
        import shared_state as _ss
        _ss.GUARDIAN_BLOCKED = True
        _ss.GUARDIAN_BLOCK_REASON = reason

    @staticmethod
    def _clear_blocked():
        import shared_state as _ss
        _ss.GUARDIAN_BLOCKED = False
        _ss.GUARDIAN_BLOCK_REASON = ""
```

- [ ] **Step 2: Call `_set_blocked` in each gate method that returns `True`**

In `is_spread_too_high`, find the line:
```python
            if current_spread > threshold:
                log.warning(
                    f"🛑 [GUARDIAN] Spread too wide — {current_spread} > {threshold} "
                    f"(AI:{ai_limit} | ATR-based:{atr_limit}) [{symbol}]")
                return True
```

Replace with:
```python
            if current_spread > threshold:
                log.warning(
                    f"🛑 [GUARDIAN] Spread too wide — {current_spread} > {threshold} "
                    f"(AI:{ai_limit} | ATR-based:{atr_limit}) [{symbol}]")
                self._set_blocked(f"Spread {current_spread}>{threshold}")
                return True
```

In `is_btc_dead_hour`, find:
```python
        if utc_hour in (0, 1):
            _log.warning(f"🛑 [GUARDIAN-E] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            return True
```

Replace with:
```python
        if utc_hour in (0, 1):
            _log.warning(f"🛑 [GUARDIAN-E] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            self._set_blocked(f"BTC dead hour UTC {utc_hour:02d}")
            return True
```

In `is_xau_dead_hour`, find:
```python
        if utc_hour in (0, 1, 9, 11, 13, 17, 18, 19, 20):
            _log.warning(f"🛑 [GUARDIAN-F] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            return True
```

Replace with:
```python
        if utc_hour in (0, 1, 9, 11, 13, 17, 18, 19, 20):
            _log.warning(f"🛑 [GUARDIAN-F] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            self._set_blocked(f"XAU dead hour UTC {utc_hour:02d}")
            return True
```

In `is_xau_sell_blocked`, find:
```python
        if str(order_type).upper() == "SELL" and allowed_dir != "SELL_ONLY":
            _log.warning(
                f"🛑 [GUARDIAN-G] Blocked {symbol} SELL — allowed_dir={allowed_dir} (need SELL_ONLY)")
            return True
```

Replace with:
```python
        if str(order_type).upper() == "SELL" and allowed_dir != "SELL_ONLY":
            _log.warning(
                f"🛑 [GUARDIAN-G] Blocked {symbol} SELL — allowed_dir={allowed_dir} (need SELL_ONLY)")
            self._set_blocked(f"XAU SELL blocked (dir={allowed_dir})")
            return True
```

In `is_xau_h4_downtrend`, find:
```python
        if "DOWNTREND" in str(h4_trend).upper():
            _log.warning(f"🛑 [GUARDIAN-I] Blocked {symbol} BUY — H4 trend={h4_trend}")
            return True
```

Replace with:
```python
        if "DOWNTREND" in str(h4_trend).upper():
            _log.warning(f"🛑 [GUARDIAN-I] Blocked {symbol} BUY — H4 trend={h4_trend}")
            self._set_blocked(f"XAU H4 downtrend")
            return True
```

In `is_score_blacklisted`, find:
```python
        if int(score) == 8:
            _log.warning(f"🛑 [GUARDIAN-H] Blocked {symbol} — score={score} blacklisted")
            return True
```

Replace with:
```python
        if int(score) == 8:
            _log.warning(f"🛑 [GUARDIAN-H] Blocked {symbol} — score={score} blacklisted")
            self._set_blocked(f"Score {score} blacklisted")
            return True
```

- [ ] **Step 3: Add `_clear_blocked()` to the two methods that log OK and return `False`**

In `is_spread_too_high`, find the OK log line:
```python
            log.info(
                f"✅ [GUARDIAN] Spread OK — {current_spread}/{threshold} pts "
                f"(AI:{ai_limit} | ATR:{atr_limit}) [{symbol}]")
            return False
```

Replace with:
```python
            self._clear_blocked()
            log.info(
                f"✅ [GUARDIAN] Spread OK — {current_spread}/{threshold} pts "
                f"(AI:{ai_limit} | ATR:{atr_limit}) [{symbol}]")
            return False
```

- [ ] **Step 4: Verify syntax**

```bash
cd "C:/Users/PC-Laptop/Documents/Dev Project/PC Trading/PC Trading 2.17"
python -c "import risk_manager; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add risk_manager.py
git commit -m "feat: set shared_state.GUARDIAN_BLOCKED on gate fire for pixel agents"
```

---

## Task 4: Wire `ANALYST_BUSY` in `ai_engine.py`

**Files:**
- Modify: `ai_engine.py` (inside `ai_analysis()`)

- [ ] **Step 1: Set `ANALYST_BUSY[symbol] = True` before the API call, `False` after**

In `ai_analysis()`, find the try block that calls `client.chat.completions.create`:
```python
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are ANALYST, a professional trading AI sniper. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=20.0
        )
        AI_IS_ONLINE = True
```

Replace with:
```python
    shared_state.ANALYST_BUSY[symbol] = True
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are ANALYST, a professional trading AI sniper. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=20.0
        )
        AI_IS_ONLINE = True
```

Then find the `except` block at the end of `ai_analysis()`:
```python
    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger(symbol).warning(f"⚠️ [ANALYST] Error: {e}")
        return {"score": 0, "decision": "HOLD", "reason": "ANALYST offline (safety block)"}
```

Replace with:
```python
    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger(symbol).warning(f"⚠️ [ANALYST] Error: {e}")
        return {"score": 0, "decision": "HOLD", "reason": "ANALYST offline (safety block)"}
    finally:
        shared_state.ANALYST_BUSY[symbol] = False
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ai_engine; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ai_engine.py
git commit -m "feat: track ANALYST_BUSY per symbol for pixel agents dashboard"
```

---

## Task 5: Create `pixel_agents.js` — sprite renderer

**Files:**
- Create: `pixel_agents.js`

- [ ] **Step 1: Create the file with sprite definitions and `drawChar`**

Create `pixel_agents.js` at the project root with this content:

```js
/**
 * pixel_agents.js — Pixel art trading floor for PC Trading dashboard.
 * Renders 6 animated characters in the right sidebar canvas.
 * Entry point: initPixelAgents('pixelAgents')
 */

'use strict';

// ── Constants ─────────────────────────────────────────────────────
const PA_SCALE = 2;       // each logical pixel = 2×2 canvas pixels (3× too tall for sidebar)
const PA_POLL_MS = 5000;  // match dashboard's fetchAndRender interval

// Sprite palettes: [headColor, bodyColor, footColor, eyeColor]
const PALETTES = {
  director:    ['#00e676', '#00b85c', '#007a3d', '#003320'],
  analystBtc:  ['#0099ff', '#006bb3', '#004477', '#001a33'],
  analystXau:  ['#ffc107', '#cc9900', '#886600', '#332200'],
  scoutBtc:    ['#00d4aa', '#009977', '#006655', '#002a22'],
  scoutXau:    ['#ff6b35', '#cc4400', '#882200', '#330d00'],
  guardian:    ['#ff3d57', '#cc1a30', '#880015', '#330008'],
  dirBearish:  ['#ff3d57', '#cc1a30', '#880015', '#330008'],
  dirNews:     ['#ffc107', '#cc9900', '#886600', '#332200'],
  dirSideway:  ['#4a5a70', '#2a3a50', '#1a2a40', '#0a1020'],
};

// Logical sprite dimensions (before scale)
const CHAR_W = 8;
const HEAD_H = 8;
const BODY_H = 6;
const LEGS_H = 4;
const CHAR_H = HEAD_H + BODY_H + LEGS_H + 2; // +2 for gaps

/**
 * Draw one pixel-art character.
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} lx  - logical x (will be multiplied by PA_SCALE)
 * @param {number} ly  - logical y (will be multiplied by PA_SCALE)
 * @param {string[]} palette  - [head, body, foot, eye]
 * @param {number} animOffset - vertical pixel offset from animation (0–3)
 * @param {boolean} dimmed    - render at 35% opacity
 */
function drawChar(ctx, lx, ly, palette, animOffset, dimmed) {
  const S = PA_SCALE;
  ctx.globalAlpha = dimmed ? 0.35 : 1.0;
  const y = ly + animOffset;

  // Head (8×8)
  ctx.fillStyle = palette[0];
  ctx.fillRect(lx * S, y * S, CHAR_W * S, HEAD_H * S);

  // Eyes (2×1 each, at row 3, cols 2 and 5)
  ctx.fillStyle = palette[3];
  ctx.fillRect((lx + 2) * S, (y + 3) * S, S, S);
  ctx.fillRect((lx + 5) * S, (y + 3) * S, S, S);

  // Body (8×6)
  ctx.fillStyle = palette[1];
  ctx.fillRect(lx * S, (y + HEAD_H + 1) * S, CHAR_W * S, BODY_H * S);

  // Legs (3px each, gap in middle)
  ctx.fillStyle = palette[2];
  ctx.fillRect(lx * S,           (y + HEAD_H + BODY_H + 2) * S, 3 * S, LEGS_H * S);
  ctx.fillRect((lx + 5) * S,     (y + HEAD_H + BODY_H + 2) * S, 3 * S, LEGS_H * S);

  ctx.globalAlpha = 1.0;
}

/**
 * Draw a speech bubble above a character.
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} lx       - logical x of character (left edge)
 * @param {number} ly       - logical y of character (top edge, pre-animOffset)
 * @param {string} text
 * @param {string} borderColor
 */
function drawBubble(ctx, lx, ly, text, borderColor) {
  const S = PA_SCALE;
  ctx.font = `bold 9px monospace`;
  const tw = ctx.measureText(text).width;
  const bw = tw + 8;
  const bh = 13;
  // Center bubble over character, clamp to canvas right edge
  let bx = lx * S + (CHAR_W * S) / 2 - bw / 2;
  bx = Math.max(1, Math.min(bx, ctx.canvas.width - bw - 1));
  const by = ly * S - bh - 3;

  if (by < 0) return; // no space above — skip

  ctx.fillStyle = 'rgba(8,14,20,0.92)';
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(bx, by, bw, bh, 2);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = borderColor;
  ctx.fillText(text, bx + 4, by + bh - 3);
}

/**
 * Draw a colored zone box (border + faint fill + label).
 */
function drawZoneBox(ctx, ly, lh, color, label) {
  const S = PA_SCALE;
  const margin = 2;
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.3;
  ctx.lineWidth = 1;
  ctx.strokeRect(margin * S, ly * S, (ctx.canvas.width - margin * 2 * S), lh * S);
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.06;
  ctx.fillRect(margin * S, ly * S, (ctx.canvas.width - margin * 2 * S), lh * S);
  ctx.globalAlpha = 1;

  ctx.fillStyle = color;
  ctx.font = 'bold 8px monospace';
  ctx.fillText(label, (margin + 2) * S, (ly + 7) * S);
}
```

- [ ] **Step 2: Verify file exists**

```bash
python -c "import os; print('OK' if os.path.exists('pixel_agents.js') else 'MISSING')"
```

Expected: `OK`

---

## Task 6: Add `StateMapper` to `pixel_agents.js`

**Files:**
- Modify: `pixel_agents.js` (append)

- [ ] **Step 1: Append `StateMapper` to the end of `pixel_agents.js`**

```js
// ── StateMapper ───────────────────────────────────────────────────
// Maps /api/status JSON → array of AgentState objects.
// AgentState: { id, palette, state, bubble, bubbleColor, dimmed }

const AGENT_IDS = ['director','analystBtc','analystXau','scoutBtc','scoutXau','guardian'];

function mapStatus(status) {
  const macro   = status.macro_data   || {};
  const strat   = status.strategy_data || {};
  const busy    = status.analyst_busy  || {};
  const gBlocked = !!status.guardian_blocked;
  const gReason  = status.guardian_block_reason || '';
  const cooldown = status.cooldown_remaining || 0;

  // ── DIRECTOR ──────────────────────────────────────────
  const dirBtc  = macro['BTCUSDm'] || macro['BTCUSDm '] || {};
  const dirXau  = macro['XAUUSDm'] || macro['XAUUSDm '] || {};
  // Use first available symbol's macro for director display
  const dirSrc  = dirBtc.bias ? dirBtc : dirXau;
  const bias    = (dirSrc.bias || 'SIDEWAY').toUpperCase();
  const allowedDir = (dirSrc.allowed_direction || 'BOTH').toUpperCase();

  let dirState, dirPalette, dirBubble, dirBubbleColor;
  if (allowedDir === 'NONE') {
    dirState = 'NEWS_PAUSE'; dirPalette = PALETTES.dirNews;
    dirBubble = '⚠️ News'; dirBubbleColor = '#ffc107';
  } else if (bias.includes('BULLISH')) {
    dirState = 'BULLISH'; dirPalette = PALETTES.director;
    dirBubble = '📈 ' + allowedDir; dirBubbleColor = '#00e676';
  } else if (bias.includes('BEARISH')) {
    dirState = 'BEARISH'; dirPalette = PALETTES.dirBearish;
    dirBubble = '📉 ' + allowedDir; dirBubbleColor = '#ff3d57';
  } else {
    dirState = 'SIDEWAY'; dirPalette = PALETTES.dirSideway;
    dirBubble = '↔ BOTH'; dirBubbleColor = '#4a5a70';
  }

  // ── ANALYST helper ────────────────────────────────────
  function analystState(sym, paletteName) {
    const isBusy = !!busy[sym];
    const lastLog = (status.logs || []).find(l => l.msg && l.msg.includes(sym) && l.msg.includes('[ANALYST]'));
    let decision = 'HOLD', score = 0;
    if (lastLog) {
      const m = lastLog.msg.match(/Decision:\s*(BUY|SELL|HOLD)/i);
      if (m) decision = m[1].toUpperCase();
      const sm = lastLog.msg.match(/Score:\s*(\d+)/i);
      if (sm) score = parseInt(sm[1]);
    }
    if (isBusy) return { state:'SCORING', bubble:'Computing...', color:'#ffc107', palette: PALETTES[paletteName], dimmed:false };
    if (decision === 'BUY')  return { state:'SIGNAL_BUY',  bubble:`🟢 BUY ${score}/15`, color:'#00e676', palette: PALETTES[paletteName], dimmed:false };
    if (decision === 'SELL') return { state:'SIGNAL_SELL', bubble:`🔴 SELL ${score}/15`, color:'#ff3d57', palette: PALETTES[paletteName], dimmed:false };
    return { state:'IDLE', bubble:'Waiting RSI...', color: PALETTES[paletteName][0], palette: PALETTES[paletteName], dimmed:false };
  }

  // ── SCOUT helper ──────────────────────────────────────
  function scoutState(sym, paletteName) {
    const regime = (strat[sym] || {}).regime || 'RANGING';
    const bullish = regime === 'TRENDING_UP' || regime === 'PULLBACK';
    const bearish = regime === 'TRENDING_DOWN' || regime === 'VOLATILE';
    if (bullish) return { state:'BULLISH', bubble:'MACD ✅', color: PALETTES[paletteName][0], palette: PALETTES[paletteName], dimmed:false };
    if (bearish) return { state:'BEARISH', bubble:'MACD ⚠️', color:'#4a5a70', palette: PALETTES[paletteName], dimmed:true };
    return { state:'NEUTRAL', bubble:'Ranging', color:'#4a5a70', palette: PALETTES[paletteName], dimmed:false };
  }

  // ── GUARDIAN ─────────────────────────────────────────
  let gState, gBubble, gColor, gPalette = PALETTES.guardian;
  if (cooldown > 0) {
    gState = 'COOLDOWN'; gBubble = `⏳ ${cooldown}m`; gColor = '#ffc107';
  } else if (gBlocked) {
    gState = 'BLOCKING'; gBubble = `🛑 ${gReason.substring(0,14)}`; gColor = '#ff3d57';
  } else {
    gState = 'CLEAR'; gBubble = '🛡 All OK'; gColor = '#00e676';
  }

  const abtResult = analystState('BTCUSDm', 'analystBtc');
  const axuResult = analystState('XAUUSDm', 'analystXau');
  const sbtResult = scoutState('BTCUSDm', 'scoutBtc');
  const sxuResult = scoutState('XAUUSDm', 'scoutXau');

  return {
    director:   { id:'director',   palette:dirPalette,         state:dirState,       bubble:dirBubble,          bubbleColor:dirBubbleColor, dimmed:false },
    analystBtc: { id:'analystBtc', palette:abtResult.palette,  state:abtResult.state, bubble:abtResult.bubble,  bubbleColor:abtResult.color, dimmed:abtResult.dimmed },
    analystXau: { id:'analystXau', palette:axuResult.palette,  state:axuResult.state, bubble:axuResult.bubble,  bubbleColor:axuResult.color, dimmed:axuResult.dimmed },
    scoutBtc:   { id:'scoutBtc',   palette:sbtResult.palette,  state:sbtResult.state, bubble:sbtResult.bubble,  bubbleColor:sbtResult.color, dimmed:sbtResult.dimmed },
    scoutXau:   { id:'scoutXau',   palette:sxuResult.palette,  state:sxuResult.state, bubble:sxuResult.bubble,  bubbleColor:sxuResult.color, dimmed:sxuResult.dimmed },
    guardian:   { id:'guardian',   palette:gPalette,           state:gState,          bubble:gBubble,            bubbleColor:gColor,          dimmed:false },
  };
}
```

- [ ] **Step 2: Verify file is valid JS**

```bash
node -e "require('./pixel_agents.js'); console.log('OK')"
```

Expected: `OK`

(If node gives "module not found" errors for `initPixelAgents` being undefined — that's expected at this stage since the function isn't defined yet. The check just confirms no syntax errors.)

---

## Task 7: Add `AnimationController`, render loop, and `initPixelAgents`

**Files:**
- Modify: `pixel_agents.js` (append)

- [ ] **Step 1: Append `AnimationController` and render loop**

```js
// ── AnimationController ───────────────────────────────────────────
// Manages per-agent animation frame and SIGNAL expiry timer.

const animCtrl = {};
AGENT_IDS.forEach(id => {
  animCtrl[id] = { frame: 0, signalExpiry: 0 };
});

function getAnimOffset(state, frame) {
  switch (state) {
    case 'BULLISH':
    case 'IDLE':
    case 'CLEAR':      return Math.round(Math.sin(frame / 20) * 1.5);
    case 'SCORING':    return Math.round(Math.sin(frame / 12) * 2.5);   // think wobble
    case 'SIGNAL_BUY':
    case 'SIGNAL_SELL':return Math.round(Math.abs(Math.sin(frame / 8)) * -4); // jump
    case 'BLOCKING':   return Math.round(Math.sin(frame / 4) * 3);     // shake
    case 'BEARISH':
    case 'COOLDOWN':   return Math.round(Math.sin(frame / 30) * 1);    // slow
    default:           return 0;
  }
}

// ── Canvas layout (logical coordinates, ÷ PA_SCALE) ──────────────
// PA_SCALE=2: canvas 220×264px → logical 110×132
// Zones stacked vertically (ly=zone top, lh=zone height in logical units):
const ZONES = {
  director: { ly: 8,   lh: 30 },  // physical y=16..76  (60px)
  btc:      { ly: 40,  lh: 30 },  // physical y=80..140
  xau:      { ly: 72,  lh: 30 },  // physical y=144..204
  guardian: { ly: 104, lh: 24 },  // physical y=208..256
};
// Character positions (lx = logical x; dly = logical y offset from zone.ly)
// dly=8 → bubble fits inside zone top (bubble height=16px, zone.ly×2+0 = start of zone)
const CHAR_POS = {
  director:   { lx: 51, dly: 8 },  // centered in 110-unit wide canvas
  analystBtc: { lx: 8,  dly: 8 },
  scoutBtc:   { lx: 54, dly: 8 },
  analystXau: { lx: 8,  dly: 8 },
  scoutXau:   { lx: 54, dly: 8 },
  guardian:   { lx: 51, dly: 8 },
};

// ── Render one frame ──────────────────────────────────────────────
let agentStates = null;  // set by poll loop

function render(ctx) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Background
  ctx.fillStyle = '#0a0e0b';
  ctx.fillRect(0, 0, W, H);

  if (!agentStates) {
    ctx.fillStyle = '#1e2a3a';
    ctx.font = '9px monospace';
    ctx.fillText('Connecting...', 8, 20);
    return;
  }

  const now = Date.now();

  // Increment per-agent frame and expire SIGNAL states
  AGENT_IDS.forEach(id => {
    animCtrl[id].frame++;
    const ag = agentStates[id];
    if (!ag) return;
    if ((ag.state === 'SIGNAL_BUY' || ag.state === 'SIGNAL_SELL') && animCtrl[id].signalExpiry === 0) {
      animCtrl[id].signalExpiry = now + 3000;  // celebrate for 3s
    }
    if (animCtrl[id].signalExpiry > 0 && now > animCtrl[id].signalExpiry) {
      ag.state = 'IDLE';
      ag.bubble = 'Waiting RSI...';
      animCtrl[id].signalExpiry = 0;
    }
  });

  // ── Draw Director zone ──
  drawZoneBox(ctx, ZONES.director.ly, ZONES.director.lh, '#00e676', '');
  const dir = agentStates.director;
  if (dir) {
    const pos = CHAR_POS.director;
    const aby = ZONES.director.ly + pos.dly;
    const off = getAnimOffset(dir.state, animCtrl.director.frame);
    drawChar(ctx, pos.lx, aby, dir.palette, off, dir.dimmed);
    drawBubble(ctx, pos.lx, aby + off, dir.bubble, dir.bubbleColor);
    ctx.fillStyle = dir.bubbleColor;
    ctx.font = 'bold 7px monospace';
    ctx.fillText('DIRECTOR', pos.lx * PA_SCALE, (aby + CHAR_H + 4) * PA_SCALE);
  }

  // ── Draw BTC zone ──
  drawZoneBox(ctx, ZONES.btc.ly, ZONES.btc.lh, '#0055aa', 'BTC');
  ['analystBtc', 'scoutBtc'].forEach(id => {
    const ag = agentStates[id];
    if (!ag) return;
    const pos = CHAR_POS[id];
    const aby = ZONES.btc.ly + pos.dly;
    const off = getAnimOffset(ag.state, animCtrl[id].frame);
    drawChar(ctx, pos.lx, aby, ag.palette, off, ag.dimmed);
    drawBubble(ctx, pos.lx, aby + off, ag.bubble, ag.bubbleColor);
    const label = id === 'analystBtc' ? 'ANALYST' : 'SCOUT';
    ctx.fillStyle = ag.dimmed ? '#2a3a50' : ag.bubbleColor;
    ctx.font = 'bold 7px monospace';
    ctx.fillText(label, pos.lx * PA_SCALE, (aby + CHAR_H + 4) * PA_SCALE);
  });

  // ── Draw XAU zone ──
  drawZoneBox(ctx, ZONES.xau.ly, ZONES.xau.lh, '#885500', 'XAU');
  ['analystXau', 'scoutXau'].forEach(id => {
    const ag = agentStates[id];
    if (!ag) return;
    const pos = CHAR_POS[id];
    const aby = ZONES.xau.ly + pos.dly;
    const off = getAnimOffset(ag.state, animCtrl[id].frame);
    drawChar(ctx, pos.lx, aby, ag.palette, off, ag.dimmed);
    drawBubble(ctx, pos.lx, aby + off, ag.bubble, ag.bubbleColor);
    const label = id === 'analystXau' ? 'ANALYST' : 'SCOUT';
    ctx.fillStyle = ag.dimmed ? '#2a3a50' : ag.bubbleColor;
    ctx.font = 'bold 7px monospace';
    ctx.fillText(label, pos.lx * PA_SCALE, (aby + CHAR_H + 4) * PA_SCALE);
  });

  // ── Draw Guardian zone ──
  drawZoneBox(ctx, ZONES.guardian.ly, ZONES.guardian.lh, '#550011', '');
  const guard = agentStates.guardian;
  if (guard) {
    const pos = CHAR_POS.guardian;
    const aby = ZONES.guardian.ly + pos.dly;
    const off = getAnimOffset(guard.state, animCtrl.guardian.frame);
    drawChar(ctx, pos.lx, aby, guard.palette, off, false);
    drawBubble(ctx, pos.lx, aby + off, guard.bubble, guard.bubbleColor);
    ctx.fillStyle = guard.bubbleColor;
    ctx.font = 'bold 7px monospace';
    ctx.fillText('GUARDIAN', pos.lx * PA_SCALE, (aby + CHAR_H + 4) * PA_SCALE);
  }
}

// ── Poll loop ─────────────────────────────────────────────────────
async function fetchAndUpdate() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const data = await res.json();
    // Preserve SIGNAL state expiry — don't overwrite an active celebration
    const newStates = mapStatus(data);
    AGENT_IDS.forEach(id => {
      if (!agentStates) return;
      const cur = agentStates[id];
      if (cur && (cur.state === 'SIGNAL_BUY' || cur.state === 'SIGNAL_SELL') && animCtrl[id].signalExpiry > Date.now()) {
        newStates[id].state = cur.state;
        newStates[id].bubble = cur.bubble;
        newStates[id].bubbleColor = cur.bubbleColor;
      }
    });
    agentStates = newStates;
  } catch (e) {
    // keep last known state on network error
  }
}

// ── Entry point ───────────────────────────────────────────────────
/**
 * @param {string} canvasId - id of <canvas> element in the right sidebar
 */
function initPixelAgents(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) { console.warn('[PixelAgents] canvas not found:', canvasId); return; }
  canvas.width  = canvas.offsetWidth || 220;
  // Compute height from bottom of last zone + 4px padding
  canvas.height = (ZONES.guardian.ly + ZONES.guardian.lh + 4) * PA_SCALE;  // = 264px

  const ctx = canvas.getContext('2d');

  // Kick off data fetch immediately, then every PA_POLL_MS
  fetchAndUpdate();
  setInterval(fetchAndUpdate, PA_POLL_MS);

  // Render loop at ~30fps (every 33ms)
  function loop() { render(ctx); requestAnimationFrame(loop); }
  loop();
}
```

- [ ] **Step 2: Verify syntax with node**

```bash
node --input-type=module < pixel_agents.js 2>&1 | head -5
```

If using CommonJS node version:
```bash
node -e "
const fs=require('fs');
const code=fs.readFileSync('pixel_agents.js','utf8');
try{ new Function(code); console.log('Syntax OK'); }
catch(e){ console.error(e.message); }
"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add pixel_agents.js
git commit -m "feat: add pixel_agents.js — canvas sprite renderer, StateMapper, AnimationController"
```

---

## Task 8: Update `dashboard.html`

**Files:**
- Modify: `dashboard.html`

- [ ] **Step 1: Add canvas and header to right sidebar**

In `dashboard.html`, find the right sidebar block (around line 606):
```html
<!-- RIGHT SIDEBAR -->
<div class="rsidebar">
  <div class="sec-title">Symbols</div>
  <div class="sym-cards" id="sym-cards"></div>
  <!-- Macro -->
  <div class="sec-title">Macro Data</div>
  <div id="macro-list"></div>
  <!-- Top Gainers -->
```

Replace with:
```html
<!-- RIGHT SIDEBAR -->
<div class="rsidebar">
  <div class="sec-title">Symbols</div>
  <div class="sym-cards" id="sym-cards"></div>
  <!-- Macro -->
  <div class="sec-title">Macro Data</div>
  <div id="macro-list"></div>
  <!-- Trading Floor -->
  <div class="sec-title" style="display:flex;align-items:center;gap:6px">
    <span style="width:6px;height:6px;border-radius:50%;background:#00e676;display:inline-block;animation:pulse 1.5s infinite"></span>
    Trading Floor
  </div>
  <div style="padding:4px 6px;background:#0a0e0b;border-bottom:1px solid var(--border)">
    <canvas id="pixelAgents" style="width:100%;display:block;image-rendering:pixelated"></canvas>
  </div>
  <!-- Top Gainers -->
```

- [ ] **Step 2: Add script tag before `</body>`**

Find the line near the bottom of `dashboard.html`:
```html
</script>
</body>
</html>
```

Replace with:
```html
</script>
<script src="pixel_agents.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', function() {
    initPixelAgents('pixelAgents');
  });
</script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add dashboard.html
git commit -m "feat: add pixel agents canvas to dashboard right sidebar"
```

---

## Task 9: Smoke test in browser

**Files:** none

- [ ] **Step 1: Start the web server**

```bash
cd "C:/Users/PC-Laptop/Documents/Dev Project/PC Trading/PC Trading 2.17"
python web_app.py
```

- [ ] **Step 2: Open dashboard and verify canvas appears**

Open `http://localhost:8080` in browser.

Check: right sidebar shows "Trading Floor" section with a dark canvas. Characters should be visible as coloured pixel-art sprites.

- [ ] **Step 3: Verify each agent state visually**

Open browser DevTools console and run:

```js
// Simulate GUARDIAN block
fetch('/api/status').then(r=>r.json()).then(d=>console.log('guardian_blocked:', d.guardian_blocked, 'analyst_busy:', d.analyst_busy, 'strategy_data:', Object.keys(d.strategy_data||{})))
```

Expected: `guardian_blocked: false  analyst_busy: {}  strategy_data: ['BTCUSDm', 'XAUUSDm']`

(strategy_data keys appear only when bot is running and has called ANALYST at least once.)

- [ ] **Step 4: Verify "Connecting..." disappears once /api/status responds**

Refresh page. Canvas should briefly show "Connecting..." then switch to characters within 5s.

- [ ] **Step 5: Final commit if adjustments made**

```bash
git add -p   # stage only intentional changes
git commit -m "fix: pixel agents canvas sizing and position adjustments"
```

---

## Known Limitations (out of scope)

- `strategy_data` fields (`regime`, `buy`, `sell`) are only populated after the bot has run at least one ANALYST cycle. On a fresh start, SCOUT will show "Ranging" until regime is set.
- `GUARDIAN_BLOCKED` is cleared only when `is_spread_too_high` returns OK — other gates don't clear it. If GUARDIAN fires gate H (score blacklist) and spread is fine afterwards, the flag stays True until the next spread check passes. Low-frequency edge case, acceptable for v1.
- Mobile layout: canvas appears below Macro Data on mobile — acceptable since trading floor is supplemental info.
