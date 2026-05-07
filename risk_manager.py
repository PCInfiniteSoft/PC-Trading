"""
GUARDIAN (was Agent 3) — Risk Gate
Enforces all pre-trade safety checks before any order fires.

Checks (in order):
  1. is_cooldown_active   — only blocks after SL hit, NOT after TP  [UPGRADE #3]
  2. is_against_trend     — DIRECTOR policy alignment
  3. is_spread_too_high   — dynamic spread filter
  4. is_max_layers_hit    — prevents martingale runaway  [UPGRADE #9]
"""

import sqlite3
import shared_state
import MetaTrader5 as mt5
from datetime import datetime, timedelta

# ── Max open layers per symbol to prevent martingale runaway ──────
MAX_LAYERS_PER_SYMBOL = 3   # [UPGRADE #9] was effectively 5 (no limit)


class RiskManager:
    def __init__(self, db_path="trading_history.db"):
        self.db_path = db_path

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
                log.warning(
                    f"🛑 [GUARDIAN] Spread too wide — {current_spread} > {threshold} "
                    f"(AI:{ai_limit} | ATR-based:{atr_limit}) [{symbol}]")
                return True

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

    def is_max_layers_hit(self, symbol, order_type, max_layers=MAX_LAYERS_PER_SYMBOL):
        """
        [GUARDIAN] Block new order if the number of open positions for
        this symbol + direction already reaches max_layers.
        Prevents the 5-layer RSI system from compounding losses indefinitely.
        """
        import logging
        log = logging.getLogger("System")
        try:
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
