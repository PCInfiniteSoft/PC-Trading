"""
GUARDIAN (was Agent 3) — Risk Gate
Enforces all pre-trade safety checks before any order fires.

Checks (in order):
  1. is_cooldown_active        — only blocks after SL hit, NOT after TP  [UPGRADE #3]
  2. is_against_trend          — DIRECTOR policy alignment
  3. is_spread_too_high        — dynamic spread filter
  4. is_max_layers_hit         — prevents martingale runaway, per-symbol cap (XAU=2) [UPGRADE #9]
  4b. is_layer_too_soon        — min spacing between same-dir layers (anti-pyramid) [Gate Q]
  5. is_btc_dead_hour          — block BTC UTC 00-01 (Asian dead zone)   [S3A Gate E]
  6. is_xau_dead_hour          — block XAU low-WR hours, UTC 00 exempt if STRONG_BULLISH [S3A Gate F]
  7. is_xau_sell_blocked       — XAU SELL only when DIRECTOR=SELL_ONLY   [S3A Gate G]
  8. is_score_blacklisted      — block score=8 anomaly band               [S3A Gate H]
  9. is_xau_h4_downtrend       — block XAU BUY when H4=DOWNTREND (0% WR) [Gate I]
 10. is_daily_budget_exhausted — block all trades when today losses >= limit [Gate J]
"""

import logging
import sqlite3
import shared_state
import MetaTrader5 as mt5
from datetime import datetime, timedelta

_log = logging.getLogger("System")

# ── Max open layers per symbol to prevent martingale runaway ──────
MAX_LAYERS_PER_SYMBOL = 3   # [UPGRADE #9] was effectively 5 (no limit)
# XAU pyramids into shared stop zones (3 shorts → one stop, 2026-06-04), so it
# gets a tighter cap than BTC. With Gate Q spacing, a tight burst is thinned to
# 2 layers (the mid attempt is dropped); this cap then hard-limits XAU to 2
# concurrent same-direction layers so a 3rd can never open.
MAX_LAYERS_XAU = 2


def _max_layers_for(symbol):
    return MAX_LAYERS_XAU if "XAU" in str(symbol).upper() else MAX_LAYERS_PER_SYMBOL


def compute_dyn_slip(price, point, atr_pct, ask, bid, cfg):
    """GUARDIAN-M dynamic slippage ceiling, in points.

    dyn = base + a*atr_pts + b*spread_pts, clamped to [slip_base, slip_cap].
    Falls back to slip_base alone when atr_pct/spread are unavailable, so the
    result is never tighter than slip_base (non-regression invariant).

    Returns (dyn_slip_pts: float, breakdown: dict) — breakdown is for logging.
    The returned dyn equals breakdown["dyn"] (both rounded) so the live
    comparison and the log line never disagree.
    """
    base = cfg.get("slip_base", 300)
    a = cfg.get("slip_a_atr", 0.0)
    b = cfg.get("slip_b_spread", 0.0)
    cap = max(cfg.get("slip_cap", base), base)  # cap must never be tighter than base

    atr_term = 0.0
    if atr_pct is not None and atr_pct > 0 and price > 0 and point > 0:
        atr_pts = (atr_pct / 100.0) * price / point
        atr_term = a * atr_pts

    spread_term = 0.0
    if ask is not None and bid is not None and point > 0:
        spread_pts = (ask - bid) / point
        if spread_pts > 0:
            spread_term = b * spread_pts

    raw = base + atr_term + spread_term
    dyn = round(min(raw, cap), 1)
    breakdown = {
        "base": base,
        "atr": round(atr_term, 1),
        "spread": round(spread_term, 1),
        "raw": round(raw, 1),
        "cap": cap,
        "dyn": dyn,
    }
    return dyn, breakdown


# ── Min spacing between same-direction layers (anti-pyramid) ──────
# [Gate Q] Prevents stacking multiple layers into a shared stop zone within
# minutes. Live failure 2026-06-04: 3 XAU shorts opened 06:00/06:10/06:15 all
# stopped together at 08:56 (-11.51). GUARDIAN-P missed it because each prior
# layer was still in profit at stack time (pyramid into winner, not average-down).
MIN_LAYER_SPACING_MINUTES = 10

# ── Cooldown after a GUARDIAN-M slip-close (churn fix #2/#3, 2026-06-06) ──────
# A GUARDIAN-M slip-close stamps shared_state.SLIP_CLOSE_AT[symbol]; this many
# minutes of no-entry follows, blocking BOTH the next pyramid layer in the same
# scan (#2) and re-entry on subsequent ticks (#3). The BTC churn re-fired on every
# slip-closed layer, bleeding spread.
SLIP_COOLDOWN_MINUTES = 5


class RiskManager:
    def __init__(self, db_path="trading_history.db"):
        self.db_path = db_path

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

    # ══════════════════════════════════════════════════════════════
    #  [UPGRADE #3] Cooldown: only block after SL, not after TP
    # ══════════════════════════════════════════════════════════════

    def is_cooldown_active(self, cooldown_minutes=5):
        """
        [GUARDIAN] Block trading only after a Stop Loss hit.
        A Take Profit close should NOT trigger cooldown — momentum may continue.
        """
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Only consider SL exits for cooldown
            cursor.execute("""
                SELECT exit_time, exit_reason
                FROM trade_history
                WHERE exit_time IS NOT NULL
                  AND exit_reason LIKE '%Stop Loss%'
                ORDER BY exit_time DESC LIMIT 1
            """)
            result = conn.fetchone() if False else cursor.fetchone()
            conn.close()

            if result and result[0]:
                last_sl_time = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
                elapsed      = datetime.now() - last_sl_time

                if elapsed < timedelta(minutes=cooldown_minutes):
                    remaining = cooldown_minutes - elapsed.total_seconds() / 60
                    import logging
                    logging.getLogger("System").warning(
                        f"⏳ [GUARDIAN] Cooldown after SL — {remaining:.1f}m remaining")
                    return True

            return False

        except Exception as e:
            import logging
            logging.getLogger("System").warning(f"⚠️ [GUARDIAN] cooldown check error: {e}")
            return False   # fail-open: don't block if DB unreadable on first run

    # ══════════════════════════════════════════════════════════════
    #  Trend alignment — DIRECTOR policy
    # ══════════════════════════════════════════════════════════════

    def is_against_trend(self, symbol, order_type):
        """[GUARDIAN] Block order if it contradicts DIRECTOR's allowed_direction."""
        macro_data  = getattr(shared_state, 'MACRO_DATA', {}).get(symbol, {})
        allowed_dir = macro_data.get('allowed_direction', 'BOTH')
        bias        = macro_data.get('bias', 'SIDEWAY')
        order       = str(order_type).upper()
        import logging
        log = logging.getLogger("System")

        if allowed_dir == "SELL_ONLY" and order == "BUY":
            log.warning(f"🛑 [GUARDIAN] Blocked BUY — DIRECTOR: {bias} ({allowed_dir})")
            return True
        if allowed_dir == "BUY_ONLY" and order == "SELL":
            log.warning(f"🛑 [GUARDIAN] Blocked SELL — DIRECTOR: {bias} ({allowed_dir})")
            return True
        if allowed_dir == "NONE":
            log.warning(f"🛑 [GUARDIAN] Blocked — DIRECTOR paused trading ({bias})")
            return True

        log.info(f"✅ [GUARDIAN] Trend OK — {order} | DIRECTOR: {allowed_dir}")
        return False

    # ══════════════════════════════════════════════════════════════
    #  Spread filter
    # ══════════════════════════════════════════════════════════════

    def is_spread_too_high(self, symbol, ai_recommended_spread):
        """
        [GUARDIAN] Dynamic spread filter.
        Calculates a fair max_spread based on current ATR instead of
        relying solely on ANALYST's static recommendation.

        Logic:
          dynamic_limit = max(ai_recommended_spread, ATR_in_points × multiplier)
          multiplier = 0.5 → allows spread up to 50% of current ATR range

        This means in volatile markets the limit widens automatically,
        and in quiet markets it tightens — without needing a new AI call.
        """
        import logging
        log = logging.getLogger("System")
        try:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                log.warning(f"⚠️ [GUARDIAN] Cannot get symbol info for {symbol}")
                return True  # block for safety

            current_spread = symbol_info.spread
            point          = symbol_info.point or 1e-5

            # ── Compute ATR-based dynamic limit ──────────────────
            import MetaTrader5 as _mt5
            import pandas as pd
            rates = _mt5.copy_rates_from_pos(symbol, _mt5.TIMEFRAME_M5, 0, 20)
            atr_limit = None
            if rates is not None and len(rates) >= 14:
                df     = pd.DataFrame(rates)
                df['tr'] = pd.concat([
                    df['high'] - df['low'],
                    (df['high'] - df['close'].shift()).abs(),
                    (df['low']  - df['close'].shift()).abs()
                ], axis=1).max(axis=1)
                atr_pts   = df['tr'].rolling(14).mean().iloc[-1] / point
                atr_limit = int(atr_pts * 0.5)   # 50% of ATR range

            # ── Choose the more permissive of ATR-limit vs AI recommendation ──
            ai_limit  = int(ai_recommended_spread) if ai_recommended_spread else 300
            threshold = max(ai_limit, atr_limit) if atr_limit else ai_limit

            if current_spread > threshold:
                self._set_blocked(f"Spread {current_spread}>{threshold}")
                log.warning(
                    f"🛑 [GUARDIAN] Spread too wide — {current_spread} > {threshold} "
                    f"(AI:{ai_limit} | ATR-based:{atr_limit}) [{symbol}]")
                return True

            self._clear_blocked()
            log.info(
                f"✅ [GUARDIAN] Spread OK — {current_spread}/{threshold} pts "
                f"(AI:{ai_limit} | ATR:{atr_limit}) [{symbol}]")
            return False

        except Exception as e:
            logging.getLogger("System").warning(f"⚠️ [GUARDIAN] spread check error: {e}")
            return True  # fail-closed on spread

    # ══════════════════════════════════════════════════════════════
    #  [UPGRADE #9] Max layers per symbol — prevent martingale runaway
    # ══════════════════════════════════════════════════════════════

    def is_max_layers_hit(self, symbol, order_type, max_layers=None, positions=None):
        """
        [GUARDIAN] Block new order if the number of open positions for
        this symbol + direction already reaches max_layers.
        Prevents the 5-layer RSI system from compounding losses indefinitely.

        max_layers defaults to a per-symbol cap (XAU=2, others=3).
        `positions` is injectable for testing.
        """
        import logging
        log = logging.getLogger("System")
        if max_layers is None:
            max_layers = _max_layers_for(symbol)
        try:
            if positions is None:
                positions = mt5.positions_get(symbol=symbol) or []
            order     = str(order_type).upper()
            mt5_type  = mt5.ORDER_TYPE_BUY if order == "BUY" else mt5.ORDER_TYPE_SELL
            count     = sum(1 for p in positions if p.type == mt5_type)

            if count >= max_layers:
                log.warning(
                    f"🛑 [GUARDIAN] Max layers hit — {symbol} {order}: "
                    f"{count}/{max_layers} open")
                return True

            log.info(f"✅ [GUARDIAN] Layers OK — {symbol} {order}: {count}/{max_layers}")
            return False

        except Exception as e:
            logging.getLogger("System").warning(f"⚠️ [GUARDIAN] max_layers check error: {e}")
            return False  # fail-open: don't block if MT5 unresponsive

    # ══════════════════════════════════════════════════════════════
    #  Gate Q — Min spacing between same-direction layers (anti-pyramid)
    # ══════════════════════════════════════════════════════════════

    def is_layer_too_soon(self, symbol, order_type,
                          min_minutes=MIN_LAYER_SPACING_MINUTES,
                          positions=None, now_ts=None):
        """[GUARDIAN-Q] Block a new same-direction layer if the most recent
        OPEN layer for this symbol+direction was opened less than `min_minutes`
        ago. Stops rapid pyramiding into a shared stop zone.

        Uses MT5 server-time POSIX timestamps on BOTH sides (position.time vs
        symbol_info_tick.time) so there is no local-vs-server timezone skew.
        `positions` / `now_ts` are injectable for testing.
        Fail-open: if MT5 is unreadable, don't block.
        """
        import logging
        log = logging.getLogger("System")
        try:
            if positions is None:
                positions = mt5.positions_get(symbol=symbol) or []
            order    = str(order_type).upper()
            mt5_type = mt5.ORDER_TYPE_BUY if order == "BUY" else mt5.ORDER_TYPE_SELL
            same     = [p for p in positions if p.type == mt5_type]
            if not same:
                return False

            last_open = max(int(p.time) for p in same)   # POSIX server time (s)

            if now_ts is None:
                tick = mt5.symbol_info_tick(symbol)
                if not (tick and tick.time):
                    return False   # fail-open: can't compare clocks safely
                now_ts = int(tick.time)

            elapsed_min = (now_ts - last_open) / 60.0
            if elapsed_min < min_minutes:
                self._set_blocked(f"Layer spacing {elapsed_min:.1f}m<{min_minutes}m")
                log.warning(
                    f"🛑 [GUARDIAN-Q] Blocked {symbol} {order} — last layer "
                    f"{elapsed_min:.1f}m ago (< {min_minutes}m spacing)")
                return True

            log.info(
                f"✅ [GUARDIAN-Q] Layer spacing OK — {symbol} {order}: "
                f"{elapsed_min:.1f}m since last layer")
            return False

        except Exception as e:
            log.warning(f"⚠️ [GUARDIAN-Q] layer spacing check error: {e}")
            return False   # fail-open

    def is_slip_cooldown_active(self, symbol, cooldown_minutes=SLIP_COOLDOWN_MINUTES,
                                now_ts=None, slip_close_at=None):
        """[GUARDIAN-S] Block entries for `cooldown_minutes` after a GUARDIAN-M
        slip-close. Covers both the next pyramid layer in the same scan (#2) and
        re-entry on subsequent ticks (#3) — the 2026-06-06 churn re-fired on every
        slip-closed layer, each paying spread.

        `slip_close_at`: POSIX server-time of this symbol's last slip-close
        (injectable; defaults to shared_state.SLIP_CLOSE_AT). `now_ts` injectable
        (defaults to the symbol tick's server time, matching is_layer_too_soon's
        clock so there is no local-vs-server skew). Fail-open."""
        import logging
        log = logging.getLogger("System")
        try:
            if slip_close_at is None:
                slip_close_at = getattr(shared_state, 'SLIP_CLOSE_AT', {}).get(symbol)
            if not slip_close_at:
                return False

            if now_ts is None:
                tick = mt5.symbol_info_tick(symbol)
                if not (tick and tick.time):
                    return False   # fail-open: can't compare clocks safely
                now_ts = int(tick.time)

            elapsed_min = (now_ts - slip_close_at) / 60.0
            if elapsed_min < cooldown_minutes:
                self._set_blocked(f"Slip cooldown {elapsed_min:.1f}m<{cooldown_minutes}m")
                log.warning(
                    f"🛑 [GUARDIAN-S] Blocked {symbol} — slip-close {elapsed_min:.1f}m "
                    f"ago (< {cooldown_minutes}m cooldown)")
                return True
            return False

        except Exception as e:
            log.warning(f"⚠️ [GUARDIAN-S] slip cooldown check error: {e}")
            return False   # fail-open

    # ══════════════════════════════════════════════════════════════
    #  [S3A] Gate E — BTC Dead-Hour Block
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _is_xau(symbol):
        return "XAU" in symbol.upper()

    def is_btc_dead_hour(self, symbol):
        """[GUARDIAN] Block BTC during UTC 00:00-01:59 (Asian dead zone, WR <32%)."""
        if "BTC" not in symbol.upper():
            return False
        utc_hour = datetime.utcnow().hour
        if utc_hour in (0, 1):
            self._set_blocked(f"BTC dead hour UTC {utc_hour:02d}")
            _log.warning(f"🛑 [GUARDIAN-E] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            return True
        return False

    # ══════════════════════════════════════════════════════════════
    #  [S3A] Gate F — XAU Dead-Hour Block
    # ══════════════════════════════════════════════════════════════

    def is_xau_dead_hour(self, symbol, macro_bias=None):
        """[GUARDIAN] Block XAU during low-WR hours.
        UTC 00 exempt when macro_bias=STRONG_BULLISH (live data: 3/3 TP in that condition).
        Extended hours: 00,01,09,11,13,17,18,19,20 (all <32% WR or 0% WR in backtest).
        """
        if not self._is_xau(symbol):
            return False
        utc_hour = datetime.utcnow().hour
        if utc_hour == 0 and macro_bias and "STRONG_BULLISH" in str(macro_bias):
            return False
        if utc_hour in (0, 1, 9, 11, 13, 17, 18, 19, 20):
            self._set_blocked(f"XAU dead hour UTC {utc_hour:02d}")
            _log.warning(f"🛑 [GUARDIAN-F] Blocked {symbol} — dead hour UTC {utc_hour:02d}:xx")
            return True
        return False

    # ══════════════════════════════════════════════════════════════
    #  [S3A] Gate G — XAU Direction Lock (BUY only)
    # ══════════════════════════════════════════════════════════════

    def is_xau_sell_blocked(self, symbol, order_type, allowed_dir='BOTH'):
        """[GUARDIAN] Block XAU SELL unless DIRECTOR macro = SELL_ONLY.
        SELL+neutral macro = -$708 PnL (18% WR). SELL+SELL_ONLY = +$261 (43% WR).
        """
        if not self._is_xau(symbol):
            return False
        if str(order_type).upper() == "SELL" and allowed_dir != "SELL_ONLY":
            self._set_blocked(f"XAU SELL blocked (dir={allowed_dir})")
            _log.warning(
                f"🛑 [GUARDIAN-G] Blocked {symbol} SELL — allowed_dir={allowed_dir} (need SELL_ONLY)")
            return True
        return False

    # ══════════════════════════════════════════════════════════════
    #  Gate I — XAU H4 Downtrend Block
    # ══════════════════════════════════════════════════════════════

    def is_xau_h4_downtrend(self, symbol, h4_trend='N/A'):
        """[GUARDIAN] Block XAU BUY when H4 trend = DOWNTREND (0% WR, 4/4 SL in backtest)."""
        if not self._is_xau(symbol):
            return False
        if "DOWNTREND" in str(h4_trend).upper():
            self._set_blocked(f"XAU H4 downtrend")
            _log.warning(f"🛑 [GUARDIAN-I] Blocked {symbol} BUY — H4 trend={h4_trend}")
            return True
        return False

    # ══════════════════════════════════════════════════════════════
    #  [S3A] Gate H — Score Anomaly Blacklist
    # ══════════════════════════════════════════════════════════════

    def is_score_blacklisted(self, symbol, score):
        """[GUARDIAN] Block score=8 entries (WR 15.8% — anomalous band)."""
        if int(score) == 8:
            self._set_blocked(f"Score {score} blacklisted")
            _log.warning(f"🛑 [GUARDIAN-H] Blocked {symbol} — score={score} blacklisted")
            return True
        return False

    # ══════════════════════════════════════════════════════════════
    #  Gate J — Daily Risk Budget
    #  Concept: the-trading-dev-kit risk-manager subagent
    # ══════════════════════════════════════════════════════════════

    def is_daily_budget_exhausted(self, daily_loss_limit: float = 100.0) -> bool:
        """[GUARDIAN-J] Block all new trades when today's realized losses >= daily_loss_limit.
        Unlike Gate D (per-symbol layers), this tracks cumulative dollar losses across ALL symbols today.
        Fail-open: if DB unreadable, don't block (first run has no history).
        """
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            today  = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
            cursor.execute("""
                SELECT COALESCE(SUM(net_profit), 0.0)
                FROM trade_history
                WHERE exit_time IS NOT NULL
                  AND exit_time >= ?
                  AND net_profit < 0
            """, (today,))
            result = cursor.fetchone()
            conn.close()
            today_losses = abs(float(result[0])) if result[0] else 0.0
            if today_losses >= daily_loss_limit:
                _log.warning(
                    f"🛑 [GUARDIAN-J] Daily budget exhausted — "
                    f"losses today ${today_losses:.2f} >= limit ${daily_loss_limit:.2f}")
                return True
            _log.info(f"✅ [GUARDIAN-J] Budget OK — today losses ${today_losses:.2f} / ${daily_loss_limit:.2f}")
            return False
        except Exception as e:
            _log.warning(f"⚠️ [GUARDIAN-J] daily budget check error: {e}")
            return False  # fail-open
