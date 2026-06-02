/**
 * pixel_agents.js — Pixel art trading floor for PC Trading dashboard.
 * Renders 6 animated characters in the right sidebar canvas.
 * Entry point: initPixelAgents('pixelAgents')
 */

'use strict';

// ── Constants ─────────────────────────────────────────────────────
const PA_SCALE = 2;       // each logical pixel = 2×2 canvas pixels
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
const CHAR_H = HEAD_H + BODY_H + LEGS_H + 2; // +2 for gaps = 20

/**
 * Draw one pixel-art character.
 */
function drawChar(ctx, lx, ly, palette, animOffset, dimmed) {
  const S = PA_SCALE;
  ctx.globalAlpha = dimmed ? 0.35 : 1.0;
  const y = ly + animOffset;

  // Head (8×8)
  ctx.fillStyle = palette[0];
  ctx.fillRect(lx * S, y * S, CHAR_W * S, HEAD_H * S);

  // Eyes (1px each at row 3, cols 2 and 5)
  ctx.fillStyle = palette[3];
  ctx.fillRect((lx + 2) * S, (y + 3) * S, S, S);
  ctx.fillRect((lx + 5) * S, (y + 3) * S, S, S);

  // Body (8×6)
  ctx.fillStyle = palette[1];
  ctx.fillRect(lx * S, (y + HEAD_H + 1) * S, CHAR_W * S, BODY_H * S);

  // Legs (3px each, gap in middle)
  ctx.fillStyle = palette[2];
  ctx.fillRect(lx * S,       (y + HEAD_H + BODY_H + 2) * S, 3 * S, LEGS_H * S);
  ctx.fillRect((lx + 5) * S, (y + HEAD_H + BODY_H + 2) * S, 3 * S, LEGS_H * S);

  ctx.globalAlpha = 1.0;
}

/**
 * Draw a speech bubble above a character.
 */
function drawBubble(ctx, lx, ly, text, borderColor) {
  const S = PA_SCALE;
  ctx.font = 'bold 9px monospace';
  const tw = ctx.measureText(text).width;
  const bw = tw + 8;
  const bh = 13;
  let bx = lx * S + (CHAR_W * S) / 2 - bw / 2;
  bx = Math.max(1, Math.min(bx, ctx.canvas.width - bw - 1));
  const by = ly * S - bh - 3;

  if (by < 0) return;

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
  const zw = ctx.canvas.width - margin * 2 * S;
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.3;
  ctx.lineWidth = 1;
  ctx.strokeRect(margin * S, ly * S, zw, lh * S);
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.06;
  ctx.fillRect(margin * S, ly * S, zw, lh * S);
  ctx.globalAlpha = 1;

  if (label) {
    ctx.fillStyle = color;
    ctx.font = 'bold 8px monospace';
    ctx.fillText(label, (margin + 2) * S, (ly + 7) * S);
  }
}

// ── StateMapper ───────────────────────────────────────────────────
const AGENT_IDS = ['director', 'analystBtc', 'analystXau', 'scoutBtc', 'scoutXau', 'guardian'];

function mapStatus(status) {
  const macro    = status.macro_data    || {};
  const strat    = status.strategy_data || {};
  const busy     = status.analyst_busy  || {};
  const gBlocked = !!status.guardian_blocked;
  const gReason  = status.guardian_block_reason || '';
  const cooldown = status.cooldown_remaining || 0;

  // ── DIRECTOR ──
  const dirBtc = macro['BTCUSDm'] || {};
  const dirXau = macro['XAUUSDm'] || {};
  const dirSrc = dirBtc.bias ? dirBtc : dirXau;
  const bias       = (dirSrc.bias || 'SIDEWAY').toUpperCase();
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

  // ── ANALYST helper ──
  function analystState(sym, paletteName) {
    const isBusy = !!busy[sym];
    const lastLog = (status.logs || []).find(
      l => l.msg && l.msg.includes(sym) && l.msg.includes('[ANALYST]')
    );
    let decision = 'HOLD', score = 0;
    if (lastLog) {
      const m = lastLog.msg.match(/Decision:\s*(BUY|SELL|HOLD)/i);
      if (m) decision = m[1].toUpperCase();
      const sm = lastLog.msg.match(/Score:\s*(\d+)/i);
      if (sm) score = parseInt(sm[1]);
    }
    if (isBusy) return { state: 'SCORING',     bubble: 'Computing...',      color: '#ffc107', palette: PALETTES[paletteName], dimmed: false };
    if (decision === 'BUY')  return { state: 'SIGNAL_BUY',  bubble: `🟢 BUY ${score}/15`, color: '#00e676', palette: PALETTES[paletteName], dimmed: false };
    if (decision === 'SELL') return { state: 'SIGNAL_SELL', bubble: `🔴 SELL ${score}/15`, color: '#ff3d57', palette: PALETTES[paletteName], dimmed: false };
    return { state: 'IDLE', bubble: 'Waiting RSI...', color: PALETTES[paletteName][0], palette: PALETTES[paletteName], dimmed: false };
  }

  // ── SCOUT helper ──
  function scoutState(sym, paletteName) {
    const regime  = (strat[sym] || {}).regime || 'RANGING';
    const bullish = regime === 'TRENDING_UP'   || regime === 'PULLBACK';
    const bearish = regime === 'TRENDING_DOWN' || regime === 'VOLATILE';
    if (bullish) return { state: 'BULLISH', bubble: 'MACD ✅', color: PALETTES[paletteName][0], palette: PALETTES[paletteName], dimmed: false };
    if (bearish) return { state: 'BEARISH', bubble: 'MACD ⚠️', color: '#4a5a70',                palette: PALETTES[paletteName], dimmed: true  };
    return         { state: 'NEUTRAL', bubble: 'Ranging',  color: '#4a5a70',                palette: PALETTES[paletteName], dimmed: false };
  }

  // ── GUARDIAN ──
  let gState, gBubble, gColor;
  if (cooldown > 0) {
    gState = 'COOLDOWN'; gBubble = `⏳ ${cooldown}m`; gColor = '#ffc107';
  } else if (gBlocked) {
    gState = 'BLOCKING'; gBubble = `🛑 ${gReason.substring(0, 14)}`; gColor = '#ff3d57';
  } else {
    gState = 'CLEAR'; gBubble = '🛡 All OK'; gColor = '#00e676';
  }

  const abt = analystState('BTCUSDm', 'analystBtc');
  const axu = analystState('XAUUSDm', 'analystXau');
  const sbt = scoutState('BTCUSDm', 'scoutBtc');
  const sxu = scoutState('XAUUSDm', 'scoutXau');

  return {
    director:   { id: 'director',   palette: dirPalette,  state: dirState,  bubble: dirBubble,  bubbleColor: dirBubbleColor, dimmed: false },
    analystBtc: { id: 'analystBtc', palette: abt.palette, state: abt.state, bubble: abt.bubble, bubbleColor: abt.color,      dimmed: abt.dimmed },
    analystXau: { id: 'analystXau', palette: axu.palette, state: axu.state, bubble: axu.bubble, bubbleColor: axu.color,      dimmed: axu.dimmed },
    scoutBtc:   { id: 'scoutBtc',   palette: sbt.palette, state: sbt.state, bubble: sbt.bubble, bubbleColor: sbt.color,      dimmed: sbt.dimmed },
    scoutXau:   { id: 'scoutXau',   palette: sxu.palette, state: sxu.state, bubble: sxu.bubble, bubbleColor: sxu.color,      dimmed: sxu.dimmed },
    guardian:   { id: 'guardian',   palette: PALETTES.guardian, state: gState, bubble: gBubble, bubbleColor: gColor,          dimmed: false },
  };
}

// ── AnimationController ───────────────────────────────────────────
const animCtrl = {};
AGENT_IDS.forEach(id => { animCtrl[id] = { frame: 0, signalExpiry: 0 }; });

function getAnimOffset(state, frame) {
  switch (state) {
    case 'BULLISH':
    case 'IDLE':
    case 'CLEAR':       return Math.round(Math.sin(frame / 20) * 1.5);
    case 'SCORING':     return Math.round(Math.sin(frame / 12) * 2.5);
    case 'SIGNAL_BUY':
    case 'SIGNAL_SELL': return Math.round(Math.abs(Math.sin(frame / 8)) * -4);
    case 'BLOCKING':    return Math.round(Math.sin(frame / 4) * 3);
    case 'BEARISH':
    case 'COOLDOWN':    return Math.round(Math.sin(frame / 30) * 1);
    default:            return 0;
  }
}

// ── Canvas layout ─────────────────────────────────────────────────
// PA_SCALE=2: canvas ~220×264px, logical ~110×132
const ZONES = {
  director: { ly: 8,   lh: 30 },
  btc:      { ly: 40,  lh: 30 },
  xau:      { ly: 72,  lh: 30 },
  guardian: { ly: 104, lh: 24 },
};
const CHAR_POS = {
  director:   { lx: 51, dly: 8 },
  analystBtc: { lx: 8,  dly: 8 },
  scoutBtc:   { lx: 54, dly: 8 },
  analystXau: { lx: 8,  dly: 8 },
  scoutXau:   { lx: 54, dly: 8 },
  guardian:   { lx: 51, dly: 8 },
};

// ── Render ────────────────────────────────────────────────────────
let agentStates = null;

function render(ctx) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0a0e0b';
  ctx.fillRect(0, 0, W, H);

  if (!agentStates) {
    ctx.fillStyle = '#1e2a3a';
    ctx.font = '9px monospace';
    ctx.fillText('Connecting...', 8, 20);
    return;
  }

  const now = Date.now();

  AGENT_IDS.forEach(id => {
    animCtrl[id].frame++;
    const ag = agentStates[id];
    if (!ag) return;
    if ((ag.state === 'SIGNAL_BUY' || ag.state === 'SIGNAL_SELL') && animCtrl[id].signalExpiry === 0) {
      animCtrl[id].signalExpiry = now + 3000;
    }
    if (animCtrl[id].signalExpiry > 0 && now > animCtrl[id].signalExpiry) {
      ag.state = 'IDLE';
      ag.bubble = 'Waiting RSI...';
      animCtrl[id].signalExpiry = 0;
    }
  });

  function drawAgent(id, zoneKey) {
    const ag = agentStates[id];
    if (!ag) return;
    const zone = ZONES[zoneKey];
    const pos  = CHAR_POS[id];
    const aby  = zone.ly + pos.dly;
    const off  = getAnimOffset(ag.state, animCtrl[id].frame);
    drawChar(ctx, pos.lx, aby, ag.palette, off, ag.dimmed);
    drawBubble(ctx, pos.lx, aby, ag.bubble, ag.bubbleColor);
    ctx.fillStyle = ag.dimmed ? '#2a3a50' : ag.bubbleColor;
    ctx.font = 'bold 7px monospace';
    ctx.fillText(id === 'director' ? 'DIRECTOR' : id === 'guardian' ? 'GUARDIAN' :
                 id === 'analystBtc' ? 'ANALYST' : id === 'analystXau' ? 'ANALYST' :
                 id === 'scoutBtc'   ? 'SCOUT'   : 'SCOUT',
                 pos.lx * PA_SCALE, (aby + CHAR_H + 4) * PA_SCALE);
  }

  drawZoneBox(ctx, ZONES.director.ly, ZONES.director.lh, '#00e676', '');
  drawAgent('director', 'director');

  drawZoneBox(ctx, ZONES.btc.ly, ZONES.btc.lh, '#0055aa', 'BTC');
  drawAgent('analystBtc', 'btc');
  drawAgent('scoutBtc',   'btc');

  drawZoneBox(ctx, ZONES.xau.ly, ZONES.xau.lh, '#885500', 'XAU');
  drawAgent('analystXau', 'xau');
  drawAgent('scoutXau',   'xau');

  drawZoneBox(ctx, ZONES.guardian.ly, ZONES.guardian.lh, '#550011', '');
  drawAgent('guardian', 'guardian');
}

// ── Poll loop ─────────────────────────────────────────────────────
async function fetchAndUpdate() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const data = await res.json();
    const newStates = mapStatus(data);
    if (agentStates) {
      AGENT_IDS.forEach(id => {
        const cur = agentStates[id];
        if (cur && (cur.state === 'SIGNAL_BUY' || cur.state === 'SIGNAL_SELL') &&
            animCtrl[id].signalExpiry > Date.now()) {
          newStates[id].state       = cur.state;
          newStates[id].bubble      = cur.bubble;
          newStates[id].bubbleColor = cur.bubbleColor;
        }
      });
    }
    agentStates = newStates;
  } catch (_) {
    // keep last known state on network error
  }
}

// ── Entry point ───────────────────────────────────────────────────
function initPixelAgents(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) { console.warn('[PixelAgents] canvas not found:', canvasId); return; }
  canvas.width  = canvas.offsetWidth || 220;
  canvas.height = (ZONES.guardian.ly + ZONES.guardian.lh + 4) * PA_SCALE; // 264px

  const ctx = canvas.getContext('2d');
  fetchAndUpdate();
  setInterval(fetchAndUpdate, PA_POLL_MS);
  function loop() { render(ctx); requestAnimationFrame(loop); }
  loop();
}
