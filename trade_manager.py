import logging
import os
import sys
import shared_state
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import ai_engine as ai
import advanced_indicators as adv
import system_utils as sutils
import database_manager as dbm
from datetime import datetime, timedelta
from bot_config import *
from trade_noti import send_trade_notification
from discord.ext import tasks
from risk_manager import RiskManager
import strategy_profile as sp

DEPLOY_FLAG = "deploy.flag"

agent3 = RiskManager(db_path="trading_history.db")

# ==========================================
# 🔌 MT5 Reconnect Watchdog
# ==========================================
import asyncio as _asyncio

async def ensure_mt5_connected(retries: int = 3, delay: float = 5.0) -> bool:
    log = logging.getLogger("System")
    if mt5.terminal_info() is not None:
        return True
    for attempt in range(1, retries + 1):
        log.warning(f"🔌 [SENTINEL] MT5 ไม่ตอบสนอง — พยายาม reconnect ครั้งที่ {attempt}/{retries}...")
        if mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV):
            shared_state.IS_MT5_DOWN = False
            log.info("✅ [SENTINEL] MT5 reconnect สำเร็จ!")
            return True
        if attempt < retries:
            await _asyncio.sleep(delay)
    shared_state.MT5_DISCONNECT_COUNT = getattr(shared_state, 'MT5_DISCONNECT_COUNT', 0) + 1
    shared_state.IS_MT5_DOWN = True
    log.error(f"🚨 [SENTINEL] MT5 reconnect ล้มเหลวทุกครั้ง! ({retries} attempts)")
    return False

def reconcile_unclosed_trades():
    """
    ทำงานครั้งเดียวตอน startup — scan DB หาไม้ที่ไม่มีข้อมูลปิด
    แล้ว backfill จาก MT5 history (ป้องกัน exit หาย เมื่อ bot restart ระหว่างที่มี position เปิด)
    """
    log = logging.getLogger("System")
    try:
        import sqlite3
        conn = sqlite3.connect(dbm.DB_NAME)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT ticket, entry_time FROM trade_history
            WHERE exit_time IS NULL OR exit_time = '' OR exit_price IS NULL
        """)
        missing = cur.fetchall()
        conn.close()

        if not missing:
            return

        log.warning(f"🔍 [STARTUP] พบ {len(missing)} ไม้ที่ขาดข้อมูลปิด — กำลัง reconcile จาก MT5 history...")

        earliest = min(datetime.strptime(r['entry_time'], "%Y-%m-%d %H:%M:%S") for r in missing)
        search_from = earliest - timedelta(days=1)
        deals = mt5.history_deals_get(search_from, datetime.now() + timedelta(hours=1))
        if not deals:
            log.warning("⚠️ [STARTUP] ไม่พบ deal ใน MT5 history สำหรับช่วงเวลานี้")
            return

        repaired = 0
        for row in missing:
            ticket = row['ticket']
            exit_deals = [d for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT]
            if not exit_deals:
                log.warning(f"⚠️ [STARTUP] ตั๋ว {ticket}: ยังเปิดอยู่หรือหา deal ไม่เจอ")
                continue
            exit_deal = exit_deals[-1]
            net_profit = exit_deal.profit + exit_deal.swap
            exit_time = datetime.fromtimestamp(exit_deal.time).strftime("%Y-%m-%d %H:%M:%S")
            reason = "Hit Take Profit 🎯 [recovered]" if exit_deal.profit > 0 else "Hit Stop Loss 🛡️ [recovered]"
            dbm.log_trade_exit(
                ticket=ticket, exit_price=exit_deal.price, net_profit=net_profit,
                max_float_p=0.0, max_float_l=0.0,
                exit_reason=reason, balance_after=0.0
            )
            log.info(f"✅ [STARTUP] Recovered ตั๋ว {ticket} | P&L: {net_profit:+.2f} | {exit_time}")
            repaired += 1

        log.info(f"✅ [STARTUP] Reconcile เสร็จ: {repaired}/{len(missing)} ไม้")
    except Exception as e:
        logging.getLogger("System").error(f"❌ [STARTUP] reconcile_unclosed_trades error: {e}")


@tasks.loop(minutes=1)
async def trading_job():

    from discord_manager import get_win_loss_text 
    
    shared_state.SCAN_COUNT += 1

    if os.path.exists(DEPLOY_FLAG):
        os.remove(DEPLOY_FLAG)
        logging.getLogger("System").info("🔄 [DEPLOY] deploy.flag detected — restarting with new code...")
        mt5.shutdown()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    if check_daily_drawdown():
        return

    if shared_state.BOT_STATE in ["STOPPED", "WAITING", "DAY-OFF"]: 
        return

    if shared_state.BOT_STATE == "COOLDOWN":
        shared_state.COOLDOWN_REMAINING -= 1 
        if shared_state.COOLDOWN_REMAINING <= 0:
            logging.getLogger("System").warning("⏳ AI ตื่นจากจำศีล กำลังตรวจสอบตลาด...")
            for s in SYMBOLS_CONFIG:
                rates = mt5.copy_rates_from_pos(s, TIMEFRAME, 0, 15)
                prices = [r['close'] for r in rates]
                volumes = [r['tick_volume'] for r in rates]
                res = await ai.ai_check_cooldown(s, prices, volumes)
                if res['status'] == "Stabilized":
                    shared_state.BOT_STATE = "RUNNING"
                    shared_state.AI_RETRY_COUNT = 0
                else:
                    shared_state.COOLDOWN_REMAINING = 30
                    return
        return

    if not await ensure_mt5_connected():
        return
    
    now = datetime.now()
    now_time = now.time()

    is_macro_time = (now.hour == 6 and now.minute == 0) or \
                    (now.hour == 13 and now.minute == 30) or \
                    (now.hour == 19 and now.minute == 0)

    last_macro = getattr(shared_state, 'LAST_MACRO_TIME', None)

    last_news_date = getattr(shared_state, 'LAST_NEWS_DATE', None)
    if last_news_date != now.date():
        shared_state.NEWS_WINDOWS = []
        shared_state.TODAY_NEWS_TIMES = []
        shared_state.LAST_NEWS_DATE = now.date()
        logging.getLogger("System").info("📅 [News] รีเซ็ต news windows สำหรับวันใหม่")

    last_atr_refresh = getattr(shared_state, 'LAST_ATR_REFRESH_TIME', datetime.min)
    atr_cooldown_ok = (now - last_atr_refresh).total_seconds() > 900

    atr_spike_detected = False
    if atr_cooldown_ok:
        for s_check in SYMBOLS_CONFIG:
            macro_d = getattr(shared_state, 'MACRO_DATA', {}).get(s_check, {})
            if macro_d.get('atr_pct_h4', 0) > ai.ATR_SPIKE_THRESHOLD:
                atr_spike_detected = True
                break

    # ตลาดเปิดแต่ยังไม่มี macro → บังคับ refresh ทันที (กรณีตลาดปิด-แล้วเปิดใหม่)
    open_without_macro = [
        s for s in SYMBOLS_CONFIG
        if is_safe_trading_time(s) and not getattr(shared_state, 'MACRO_DATA', {}).get(s)
    ]
    if open_without_macro:
        logging.getLogger("System").warning(
            f"⚠️ [DIRECTOR] ตลาดเปิดแต่ยังไม่มี Macro: {open_without_macro} — บังคับ refresh")

    needs_macro_refresh = (
        (is_macro_time or last_macro is None or atr_spike_detected or bool(open_without_macro))
        and (last_macro is None or last_macro.minute != now.minute or bool(open_without_macro))
    )

    if needs_macro_refresh:
        if atr_spike_detected:
            shared_state.LAST_ATR_REFRESH_TIME = now
            logging.getLogger("System").warning("⚡ [DIRECTOR] ATR spike — triggering early macro refresh")
        else:
            logging.getLogger("System").info("👑 [DIRECTOR] กำลังประเมินทิศทางตลาดและข่าวสาร (Macro Bias)...")
        
        try:
            symbols_list = list(SYMBOLS_CONFIG.keys())
            news_list = ai.get_today_high_impact_news(symbols_list)
            today_news = " | ".join(news_list)
        except Exception as e:
            logging.getLogger("System").warning(f"⚠️ ดึงข่าวไม่สำเร็จ: {e}")
            today_news = "No news data available"

        for macro_symbol in SYMBOLS_CONFIG:
            if not is_safe_trading_time(macro_symbol):
                logging.getLogger("System").info(
                    f"⏭️ [DIRECTOR] ข้าม {macro_symbol} — ตลาดปิด ไม่วิเคราะห์ Macro")
                continue
            macro_trends     = adv.get_macro_trends(macro_symbol)
            h4_trend_status  = macro_trends["h4_trend"]
            d1_trend_status  = macro_trends["d1_trend"]
            atr_pct_h4       = macro_trends.get("atr_pct_h4", 0.0)
            await ai.ai_macro_analysis(macro_symbol, h4_trend_status, d1_trend_status,
                                       today_news, atr_pct_h4=atr_pct_h4)
        
        shared_state.LAST_MACRO_TIME = now

    # ── Weekly / Monthly Analysis trigger ─────────────────────────
    now_utc    = datetime.utcnow()
    _is_sunday = (now_utc.weekday() == 6 and now_utc.hour == 0)
    _cur_week  = now_utc.isocalendar()[1]
    _last_week = getattr(shared_state, 'LAST_WEEKLY_ANALYSIS_WEEK', None)

    if _is_sunday and _last_week != _cur_week:
        try:
            import weekly_analyzer
            _wpath = await _asyncio.get_event_loop().run_in_executor(
                None, weekly_analyzer.run_weekly_analysis)
            if _wpath:
                logging.getLogger("System").info(f"📊 [Weekly] analysis เสร็จ: {_wpath}")
            shared_state.LAST_WEEKLY_ANALYSIS_WEEK = _cur_week
        except Exception as _e:
            logging.getLogger("System").error(f"❌ [Weekly] error: {_e}")

        if now_utc.day <= 7:
            if mt5.terminal_info() is not None:
                try:
                    import monthly_analyzer
                    _task = _asyncio.create_task(monthly_analyzer.run_monthly_analysis())
                    shared_state.MONTHLY_TASK_REF = _task
                except Exception as _e:
                    logging.getLogger("System").error(f"❌ [Monthly] error: {_e}")
            else:
                shared_state.MONTHLY_ANALYSIS_PENDING = True
                logging.getLogger("System").warning("📊 [Monthly] MT5 offline — retry ทุก 15 นาที")

    if getattr(shared_state, 'MONTHLY_ANALYSIS_PENDING', False):
        _last_retry = getattr(shared_state, 'MONTHLY_RETRY_TIME', datetime.min)
        if (now_utc - _last_retry).total_seconds() >= 900 and mt5.terminal_info() is not None:
            try:
                import monthly_analyzer
                _task = _asyncio.create_task(monthly_analyzer.run_monthly_analysis())
                shared_state.MONTHLY_TASK_REF = _task
                shared_state.MONTHLY_ANALYSIS_PENDING = False
                shared_state.MONTHLY_RETRY_TIME = now_utc
            except Exception as _e:
                logging.getLogger("System").error(f"❌ [Monthly retry] error: {_e}")

    is_news_window = False
    news_windows = getattr(shared_state, 'NEWS_WINDOWS', [])
    if news_windows:
        for nw in news_windows:
            if nw["start"] <= now_time <= nw["end"]:
                is_news_window = True
                break
    else:
        for nt in getattr(shared_state, 'TODAY_NEWS_TIMES', []):
            news_dt      = datetime.combine(now.date(), nt)
            start_window = (news_dt - timedelta(minutes=5)).time()
            end_window   = (news_dt + timedelta(minutes=15)).time()
            if start_window <= now_time <= end_window:
                is_news_window = True
                break

    if is_news_window and shared_state.CURRENT_LOOP_MINS != 1:
        shared_state.CURRENT_LOOP_MINS = 1
        logging.getLogger("System").warning("🚨 [HYPER-ACTIVE MODE] เข้าสู่ช่วงข่าวกล่องแดง! AI สแกนทุก 1 นาที")
    elif not is_news_window and shared_state.CURRENT_LOOP_MINS == 1:
        shared_state.CURRENT_LOOP_MINS = 5
        logging.getLogger("System").info("✅ [NORMAL MODE] พ้นช่วงข่าวกล่องแดง ปรับรอบสแกนกลับเป็น 5 นาที")

    last_ai_update = getattr(shared_state, 'LAST_AI_UPDATE_TIME', datetime.min)
    time_since_update = (now - last_ai_update).total_seconds() / 60

    if shared_state.CURRENT_LOOP_MINS == 1:
        is_ai_update_turn = True
    else:
        is_ai_update_turn = (time_since_update >= 4.5)

    if is_ai_update_turn and not ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = getattr(shared_state, 'AI_RETRY_COUNT', 0) + 1
        logging.getLogger("System").warning(f"⚠️ พยายามปลุก AI ให้ตื่น (รอบที่ {shared_state.AI_RETRY_COUNT}/5)...")
        
    elif ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = 0 
        if is_ai_update_turn:
            shared_state.LAST_AI_UPDATE_TIME = now 

    if getattr(shared_state, 'AI_RETRY_COUNT', 0) >= 5:
        shared_state.BOT_STATE = "COOLDOWN"
        shared_state.COOLDOWN_REMAINING = 30
        shared_state.AI_RETRY_COUNT = 0 
        shared_state.AI_DISCONNECT_COUNT = getattr(shared_state, 'AI_DISCONNECT_COUNT', 0) + 1
        logging.getLogger("System").error("🚨 สัญญาณ AI ขาดหายเกิน 5 นาที! เข้าสู่โหมดจำศีลเพื่อความปลอดภัย")
        return

    has_spike = False
    for s in SYMBOLS_CONFIG:
        rsi = get_rsi(s) 
        if rsi is None: continue
        
        if not is_safe_trading_time(s):
            continue
            
        is_volatile = check_volatility(s, ai.STRATEGY_DATA[s]["threshold"]) 
        
        if is_ai_update_turn or is_volatile or not ai.AI_IS_ONLINE:
            if is_volatile: 
                has_spike = True
            
            await ai.ai_update_strategy(s, get_win_loss_text())
            if not mt5.positions_get(symbol=s):
                async with shared_state.trade_layers_lock:
                    shared_state.TRADE_LAYERS[s] = {"buy": [False]*5, "sell": [False]*5}

        strat = ai.STRATEGY_DATA[s]

        # Safety valve — NONE > 2 hours → force BOTH
        macro_data_check = getattr(shared_state, 'MACRO_DATA', {}).get(s, {})
        none_since = macro_data_check.get("none_since")
        if none_since and (datetime.now() - none_since).total_seconds() > 7200:
            logging.getLogger("System").warning(
                f"⚠️ [DIRECTOR] NONE มานานกว่า 2 ชั่วโมงแล้ว — รีเซ็ตเป็น BOTH อัตโนมัติ ({s})")
            shared_state.MACRO_DATA[s]["allowed_direction"] = "BOTH"
            shared_state.MACRO_DATA[s]["bias"] = "SIDEWAY"
            shared_state.MACRO_DATA[s].pop("none_since", None)
            shared_state.CURRENT_RISK_LEVEL = 3

        if is_ai_update_turn:
            buy_target = strat['buy'][0] if strat.get('buy') else '--'
            logging.getLogger(s).info(f"📊 RSI: {rsi:.2f} | เป้าซื้อ AI: < {buy_target} | ตลาด: {strat['regime']}")

        # ══════════════════════════════════════════════════════════════
        # [FIX] DIRECTOR DATA GATE
        # บล็อกการเทรดทั้งหมด (BUY + SELL) จนกว่า DIRECTOR จะวิเคราะห์อย่างน้อย 1 ครั้ง
        # ป้องกันเทรดโดยไม่มีบริบทตลาด (ปัญหา 20 ไม้แรกก่อน macro bias feature)
        # ══════════════════════════════════════════════════════════════
        if not macro_data_check:
            if is_ai_update_turn:
                logging.getLogger(s).warning(
                    f"⛔ [DIRECTOR GATE] ยังไม่มีข้อมูล Macro ของ {s} — รอ DIRECTOR วิเคราะห์ก่อน")
            continue

        safe_buy = strat['buy'][0] if strat.get('buy') else 0
        if safe_buy > 100:
            logging.getLogger("System").error(f"🚨 บล็อกการเทรด {s}: AI ส่งเป้าหมายราคา ({safe_buy}) แทน RSI!")
            continue

        positions = mt5.positions_get(symbol=s)
        has_buy = False
        has_sell = False
        buy_tickets = []
        sell_tickets = []
        
        if positions:
            for p in positions:
                if p.type == mt5.ORDER_TYPE_BUY:
                    has_buy = True
                    buy_tickets.append(p.ticket)
                elif p.type == mt5.ORDER_TYPE_SELL:
                    has_sell = True
                    sell_tickets.append(p.ticket)

        if len(strat.get('buy', [])) < 5 or len(strat.get('sell', [])) < 5:
            logging.getLogger(s).warning(f"⚠️ STRATEGY_DATA สำหรับ {s} ไม่ครบ — ข้ามรอบนี้")
            continue

        # Candle fingerprint — skip entry logic if M5 candle hasn't changed
        _rates_fp = mt5.copy_rates_from_pos(s, TIMEFRAME, 0, 1)
        if _rates_fp is not None and len(_rates_fp) > 0:
            _candle_ts = int(_rates_fp[0]['time'])
            if _candle_ts == shared_state.LAST_CANDLE_TIME.get(s):
                continue
            shared_state.LAST_CANDLE_TIME[s] = _candle_ts

        for i in range(5):

            # ==========================================
            # 📉 BUY block
            # ==========================================
            if rsi < strat['buy'][i]:
                if has_sell:
                    for t in sell_tickets:
                        close_one_order(symbol=s, reason="RSI Hit Buy Target (TP) 🎯", ticket=t)
                    has_sell = False
                    
                if not shared_state.TRADE_LAYERS.get(s, {}).get("buy", [False]*5)[i]:
                    
                    macro_data = getattr(shared_state, 'MACRO_DATA', {}).get(s, {})
                    allowed_dir = macro_data.get('allowed_direction', 'BOTH')
                    
                    if allowed_dir in ["BUY_ONLY", "BOTH"]:
                        log = logging.getLogger(s)

                        # Pre-flight: deterministic guards before any AI call.
                        # Global guards use break (RSI<buy and RSI>sell are mutually exclusive,
                        # so breaking BUY layers never suppresses valid SELL entries).
                        if agent3.is_daily_budget_exhausted(DAILY_LOSS_LIMIT):
                            log.warning(f"🛑 [GUARDIAN-J] บล็อก! Daily budget exhausted — ไม่เปิด BUY {s}"); break
                        if agent3.is_cooldown_active(cooldown_minutes=5):
                            log.warning("⏳ [GUARDIAN] บล็อก! ติด Cooldown (after SL)"); continue
                        if agent3.is_against_trend(s, "BUY"):
                            log.warning("🛑 [GUARDIAN] บล็อก! BUY สวนนโยบาย DIRECTOR"); break
                        if agent3.is_spread_too_high(s, strat.get('max_spread', 5000)):
                            log.warning(f"⚠️ [GUARDIAN] บล็อก! Spread กว้างเกิน"); break
                        if agent3.is_max_layers_hit(s, "BUY"):
                            log.warning(f"🛑 [GUARDIAN] บล็อก! ถึงขีดจำกัด {s} BUY layers"); break
                        if agent3.is_layer_too_soon(s, "BUY"):
                            log.warning(f"🛑 [GUARDIAN-Q] บล็อก! {s} BUY layer ชิดเกิน (anti-pyramid spacing)"); break
                        if agent3.is_slip_cooldown_active(s):
                            log.warning(f"🛑 [GUARDIAN-S] บล็อก! {s} BUY — slip-close cooldown active"); break
                        if agent3.is_btc_dead_hour(s):
                            log.warning(f"🛑 [GUARDIAN-E] บล็อก! {s} Dead Hour (UTC 00-01)"); break
                        if agent3.is_xau_dead_hour(s, macro_data.get('bias', '')):
                            log.warning(f"🛑 [GUARDIAN-F] บล็อก! {s} Dead Hour"); break
                        if agent3.is_xau_h4_downtrend(s, macro_data.get('h4_trend', 'N/A')):
                            log.warning(f"🛑 [GUARDIAN-I] บล็อก! {s} BUY — H4 DOWNTREND"); break
                        if "DOWNTREND" in str(macro_data.get('d1_trend', '')):
                            log.warning(f"🛑 [GUARDIAN-L] บล็อก! {s} BUY — D1 DOWNTREND"); break
                        if sp.guard_enabled(s, "btc_momentum_guards") and "UPTREND" not in str(macro_data.get('h4_trend', '')):
                            log.warning(f"🛑 [GUARDIAN-N] บล็อก! {s} BUY — H4 ไม่ใช่ UPTREND ({macro_data.get('h4_trend', 'N/A')})"); break

                        st_data = adv.get_3_indicators(s)
                        if "DOWNTREND" in str(st_data.get('supertrend_h1', '')):
                            log.warning(f"🛑 [GUARDIAN-K] บล็อก! {s} BUY — H1 Supertrend DOWNTREND"); break

                        scout = adv.get_scout_score(s, "BUY")
                        log.info(f"🔭 [SCOUT] BUY pre-check — {scout['reason']}")
                        if sp.guard_enabled(s, "btc_momentum_guards") and scout.get('macd_signal') == "BEARISH":
                            log.warning(f"🛑 [GUARDIAN-O] บล็อก! {s} BUY — MACD BEARISH (momentum ยังลง)"); break
                        if ai.AI_IS_ONLINE:
                            tick = mt5.symbol_info_tick(s)
                            if tick is not None:
                                ans = await ai.ai_analysis(s, tick.ask, rsi, st_data)
                                log.info(f"🧠 [ANALYST] Score: {ans.get('score')} | Action: {ans.get('decision')} | Reason: {ans.get('reason')}")

                                risk = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
                                t_score = 4 if risk == 5 else 5 if risk == 4 else 6 if risk == 3 else 7 if risk == 2 else 8

                                # ── ดึง per-symbol score offset ──────────────────────
                                sym_cfg = SYMBOLS_CONFIG.get(s, {})
                                t_score = max(5, t_score + sym_cfg.get("analyst_score_offset", 0))

                                score = round(float(ans.get('score', 0)), 1)

                                # ══════════════════════════════════════════════════════
                                # [BUG FIX] ANALYST Decision check
                                # ต้องการทั้ง decision=="BUY" AND score>=t_score
                                # "HOLD" หรือ "SELL" ต้องไม่ trigger BUY ไม่ว่า score จะสูงแค่ไหน
                                # ══════════════════════════════════════════════════════
                                if ans.get('decision') == "BUY" and score >= t_score:
                                    if i > 0:
                                        losing = [p for p in (positions or []) if p.type == mt5.ORDER_TYPE_BUY and p.profit < 0]
                                        if losing:
                                            log.warning(f"🛑 [GUARDIAN-P] บล็อก! L{i+1} — L{i} ยังขาดทุน ({losing[0].profit:.2f} USD)"); continue
                                    if agent3.is_score_blacklisted(s, score):
                                        log.warning(f"🛑 [GUARDIAN-H] บล็อก! Score={score} anomaly band"); continue

                                    log.info(f"🎯 [Python] ผ่านด่าน Agent 3 แล้ว! กำลังส่งคำสั่ง BUY → MT5 (SENTINEL OK)...")

                                    sym_info = mt5.symbol_info(s)
                                    spread_now = round((tick.ask - tick.bid) / sym_info.point, 1) if sym_info else None
                                    if place_order(s, "BUY", tick.ask, rsi, f"L{i+1}:Score={score}",
                                                   extra={"analyst_score": score, "scout_score": scout.get("score"), "spread": spread_now}):
                                        async with shared_state.trade_layers_lock:
                                            shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["buy"][i] = True
                                        has_buy = True
                                        break  # [FIX] 1 layer per scan — ป้องกัน multi-entry ใน loop เดียว
                                    else:
                                        log.error(f"❌ [MT5] ยิงคำสั่ง BUY ไม่เข้า! (MT5 ปฏิเสธการเข้าออเดอร์)")

            # ==========================================
            # 📈 SELL block
            # ==========================================
            if rsi > strat['sell'][i]:
                if has_buy:
                    for t in buy_tickets:
                        close_one_order(symbol=s, reason="RSI Hit Sell Target (TP) 🎯", ticket=t)
                    has_buy = False

                if not shared_state.TRADE_LAYERS.get(s, {}).get("sell", [False]*5)[i]:
                    
                    macro_data = getattr(shared_state, 'MACRO_DATA', {}).get(s, {})
                    allowed_dir = macro_data.get('allowed_direction', 'BOTH')
                    
                    if allowed_dir in ["SELL_ONLY", "BOTH"]:
                        log = logging.getLogger(s)

                        # Pre-flight: deterministic guards before any AI call.
                        if agent3.is_daily_budget_exhausted(DAILY_LOSS_LIMIT):
                            log.warning(f"🛑 [GUARDIAN-J] บล็อก! Daily budget exhausted — ไม่เปิด SELL {s}"); break
                        if agent3.is_cooldown_active(cooldown_minutes=5):
                            log.warning("⏳ [GUARDIAN] บล็อก! ติด Cooldown (after SL)"); continue
                        if agent3.is_against_trend(s, "SELL"):
                            log.warning("🛑 [GUARDIAN] บล็อก! SELL สวนนโยบาย DIRECTOR"); break
                        if agent3.is_spread_too_high(s, strat.get('max_spread', 5000)):
                            log.warning(f"⚠️ [GUARDIAN] บล็อก! Spread กว้างเกิน"); break
                        if agent3.is_max_layers_hit(s, "SELL"):
                            log.warning(f"🛑 [GUARDIAN] บล็อก! ถึงขีดจำกัด {s} SELL layers"); break
                        if agent3.is_layer_too_soon(s, "SELL"):
                            log.warning(f"🛑 [GUARDIAN-Q] บล็อก! {s} SELL layer ชิดเกิน (anti-pyramid spacing)"); break
                        if agent3.is_slip_cooldown_active(s):
                            log.warning(f"🛑 [GUARDIAN-S] บล็อก! {s} SELL — slip-close cooldown active"); break
                        if agent3.is_btc_dead_hour(s):
                            log.warning(f"🛑 [GUARDIAN-E] บล็อก! {s} Dead Hour (UTC 00-01)"); break
                        if agent3.is_xau_dead_hour(s, macro_data.get('bias', '')):
                            log.warning(f"🛑 [GUARDIAN-F] บล็อก! {s} Dead Hour"); break
                        if agent3.is_xau_sell_blocked(s, "SELL", allowed_dir):
                            log.warning(f"🛑 [GUARDIAN-G] บล็อก! {s} XAU SELL blocked (need SELL_ONLY)"); break
                        if "UPTREND" in str(macro_data.get('d1_trend', '')):
                            log.warning(f"🛑 [GUARDIAN-L] บล็อก! {s} SELL — D1 UPTREND"); break

                        # ── Trend-sell (st3) path — alternative SELL trigger (XAU only).
                        # Inherits every deterministic guard above (J/cooldown/against_trend/
                        # spread/max_layers/Q/E/F/G/L). Fully inert until xau_trend_sell_enabled.
                        if sp.trend_sell_enabled(s) and sp.has_path(s, "trend_sell"):
                            m5_closes = adv.get_recent_m5_closes(s, bars=120)
                            if sp.trend_sell_signal(s, m5_closes, macro_data.get('d1_trend', '')):
                                # GUARDIAN-P: don't add to a losing SELL layer (mirror of AI path)
                                if i > 0:
                                    losing = [p for p in (positions or []) if p.type == mt5.ORDER_TYPE_SELL and p.profit < 0]
                                    if losing:
                                        # break (not continue like the AI path): trend-sell abandons the
                                        # whole bar when a lower layer is losing — never pyramids into weakness.
                                        log.warning(f"🛑 [GUARDIAN-P] บล็อก! TS L{i+1} — L{i} ยังขาดทุน ({losing[0].profit:.2f} USD)"); break
                                ts_tick = mt5.symbol_info_tick(s)
                                if ts_tick is None:
                                    log.warning(f"⚠️ [TREND-SELL] symbol_info_tick None for {s} — skip entry"); continue
                                ts_mult = sp.trend_sell_cfg(s).get("lot_mult", 0.5)
                                log.info(f"📉 [TREND-SELL] st3 fired {s} — SELL (lot_mult={ts_mult})")
                                if place_order(s, "SELL", ts_tick.bid, rsi, f"TS:st3:L{i+1}",
                                               extra={"entry_reason": "trend_sell", "scout_score": None, "lot_mult": ts_mult}):
                                    async with shared_state.trade_layers_lock:
                                        shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True
                                    break   # bar claimed by trend-sell
                            # if signal did not fire, fall through to the normal mean-reversion AI path below

                        st_data = adv.get_3_indicators(s)
                        scout = adv.get_scout_score(s, "SELL")
                        log.info(f"🔭 [SCOUT] SELL pre-check — {scout['reason']}")
                        if ai.AI_IS_ONLINE:
                            tick = mt5.symbol_info_tick(s)
                            if tick is not None:
                                ans = await ai.ai_analysis(s, tick.bid, rsi, st_data)
                                log.info(f"🧠 [ANALYST] Score: {ans.get('score')} | Action: {ans.get('decision')} | Reason: {ans.get('reason')}")

                                risk = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
                                t_score = 4 if risk == 5 else 5 if risk == 4 else 6 if risk == 3 else 7 if risk == 2 else 8

                                # ── ดึง per-symbol score offset ──────────────────────
                                sym_cfg = SYMBOLS_CONFIG.get(s, {})
                                t_score = max(5, t_score + sym_cfg.get("analyst_score_offset", 0))

                                score = round(float(ans.get('score', 0)), 1)

                                # ══════════════════════════════════════════════════════
                                # [BUG FIX] ANALYST Decision check
                                # ต้องการทั้ง decision=="SELL" AND score>=t_score
                                # "HOLD" หรือ "BUY" ต้องไม่ trigger SELL ไม่ว่า score จะสูงแค่ไหน
                                # ══════════════════════════════════════════════════════
                                if ans.get('decision') == "SELL" and score >= t_score:
                                    if agent3.is_score_blacklisted(s, score):
                                        log.warning(f"🛑 [GUARDIAN-H] บล็อก! Score={score} anomaly band"); continue
                                    if i > 0:
                                        losing = [p for p in (positions or []) if p.type == mt5.ORDER_TYPE_SELL and p.profit < 0]
                                        if losing:
                                            log.warning(f"🛑 [GUARDIAN-P] บล็อก! L{i+1} — L{i} ยังขาดทุน ({losing[0].profit:.2f} USD)"); continue

                                    log.info(f"🎯 [Python] ผ่านด่าน Agent 3 แล้ว! กำลังส่งคำสั่ง SELL → MT5 (SENTINEL OK)...")

                                    sym_info = mt5.symbol_info(s)
                                    spread_now = round((tick.bid - tick.ask) / sym_info.point * -1, 1) if sym_info else None
                                    if place_order(s, "SELL", tick.bid, rsi, f"L{i+1}:Score={score}",
                                                   extra={"analyst_score": score, "scout_score": scout.get("score"), "spread": spread_now}):
                                        async with shared_state.trade_layers_lock:
                                            shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True
                                        has_sell = True
                                        break  # [FIX] 1 layer per scan — ป้องกัน multi-entry ใน loop เดียว
                                    else:
                                        log.error(f"❌ [MT5] ยิงคำสั่ง SELL ไม่เข้า! (MT5 ปฏิเสธการเข้าออเดอร์)")

    positions = mt5.positions_get()
    current_tickets = []
    if positions:
        for pos in positions:
            ticket = pos.ticket
            symbol = pos.symbol
            current_tickets.append(ticket)
            
            if ticket not in shared_state.ACTIVE_TRADE_TRACKER:
                shared_state.ACTIVE_TRADE_TRACKER[ticket] = {"max_p": 0.0, "max_l": 0.0}
                
            current_p = pos.profit + pos.swap
                
            if current_p > shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"]:
                shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"] = current_p
                
            if current_p < shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"]:
                shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"] = current_p

            max_profit = shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"]
            strat = ai.STRATEGY_DATA.get(symbol, {})
            
            TP_ACTIVATION = strat.get("tp_activation", 3.0)     
            PULLBACK_PCT = strat.get("pullback_pct", 0.30)      
            BE_ACTIVATION = strat.get("be_activation", 1.50)    
            BE_LOCK_PROFIT = strat.get("be_lock_profit", 0.20)  

            symbol_info = mt5.symbol_info(symbol)
            point_value_per_lot = symbol_info.trade_tick_value / symbol_info.trade_tick_size
            
            new_sl_price = 0.0

            if max_profit >= TP_ACTIVATION:
                locked_profit_usd = max_profit * (1.0 - PULLBACK_PCT)
                # ── [FIX] ป้องกัน lock ที่น้อยเกินไปจนเสีย spread/swap ──
                # ต้องมีกำไรที่จะ lock อย่างน้อย $1.00 ไม่งั้น SL ใกล้ราคาเปิดเกินไป
                if locked_profit_usd >= 1.0:
                    price_dist = locked_profit_usd / (pos.volume * point_value_per_lot)

                    if pos.type == mt5.ORDER_TYPE_BUY:
                        new_sl_price = pos.price_open + price_dist
                    else:
                        new_sl_price = pos.price_open - price_dist

                    reason = "Trailing Profit 🏃‍♂️💨"

            elif max_profit >= BE_ACTIVATION:
                price_dist = BE_LOCK_PROFIT / (pos.volume * point_value_per_lot)
                
                if pos.type == mt5.ORDER_TYPE_BUY:
                    new_sl_price = pos.price_open + price_dist
                else:
                    new_sl_price = pos.price_open - price_dist
                    
                reason = "Break-Even 🛡️"

            if new_sl_price > 0:
                new_sl_price = round(new_sl_price, symbol_info.digits)
                is_better = (new_sl_price > pos.sl) if pos.type == mt5.ORDER_TYPE_BUY else (new_sl_price < pos.sl or pos.sl == 0)
                
                if is_better:
                    if modify_position_sltp(ticket, symbol, new_sl_price, pos.tp):
                        logging.getLogger(symbol).info(f"✅ [ANALYST] ขยับ SL ล็อกกำไร ({reason}) -> {new_sl_price}")

    # ==========================================
    # 🟢 ตามเก็บตกไม้ที่ถูกโบรคเกอร์ปิด
    # ==========================================
    closed_tickets = []
    for ticket in list(shared_state.ACTIVE_TRADE_TRACKER.keys()):
        if ticket not in current_tickets:
            closed_tickets.append(ticket)
            
    for ticket in closed_tickets:
        now = datetime.now()
        # [BUG FIX] ขยาย search window จาก "แค่วันนี้" → 7 วันย้อนหลัง
        # เดิมใช้ start_of_day ทำให้หา deal ไม่เจอถ้า position ปิดคนละวันกับที่ bot restart
        search_from = now - timedelta(days=7)
        deals = mt5.history_deals_get(search_from, now, group="*")

        if deals:
            exit_deals = [d for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT]
            if exit_deals:
                exit_deal = exit_deals[-1]
                net_profit = exit_deal.profit + exit_deal.swap
                tracker = shared_state.ACTIVE_TRADE_TRACKER[ticket]

                acc = mt5.account_info()
                curr_bal = acc.balance if acc else 0.0
                reason = "Hit Take Profit 🎯" if exit_deal.profit > 0 else "Hit Stop Loss 🛡️"

                dbm.log_trade_exit(
                    ticket=ticket, exit_price=exit_deal.price, net_profit=net_profit,
                    max_float_p=tracker["max_p"], max_float_l=tracker["max_l"],
                    exit_reason=reason, balance_after=curr_bal
                )
                logging.getLogger("System").info(f"📝 ตามเก็บ Record ที่ถูกโบรคเกอร์ปิดลง DB: ตั๋ว {ticket} ({reason})")
            else:
                logging.getLogger("System").warning(f"⚠️ [TRACKER] ตั๋ว {ticket} หลุด tracker แต่หา exit deal ไม่เจอใน 7 วัน")
        
        async with shared_state.tracker_lock:
            del shared_state.ACTIVE_TRADE_TRACKER[ticket]

    try:
        acc = mt5.account_info()
        positions = mt5.positions_get()
        if acc:
            eq = acc.equity
            pos_count = len(positions) if positions else 0
            float_pl = sum([(p.profit + p.swap) for p in positions]) if positions else 0.0
            
            state_str = shared_state.BOT_STATE
            if state_str == "COOLDOWN":
                state_str = f"COOLDOWN ({shared_state.COOLDOWN_REMAINING}m)"
                
            logging.getLogger("System").info(
                f"💓 [Heartbeat] {state_str} | Gear: {shared_state.CURRENT_LOOP_MINS}m | "
                f"ถือ: {pos_count} ไม้ | Float: {float_pl:+.2f} USD | Equity: {eq:.2f} USD"
            )
    except Exception as e:
        pass
        
    if has_spike:
        shared_state.FAST_GEAR_COUNT += 1
        if shared_state.FAST_GEAR_COUNT >= 3:
            shared_state.BOT_STATE = "COOLDOWN"
            shared_state.COOLDOWN_REMAINING = 30
            shared_state.FAST_GEAR_COUNT = 0
            logging.getLogger("System").error("🚨 Circuit Breaker Active! ตลาดกระชากแรงต่อเนื่อง พัก 30 นาที")
            return
    else:
        shared_state.FAST_GEAR_COUNT = 0
        
    sutils.save_web_status()

@trading_job.before_loop
async def before_trading_job():
    import asyncio
    from datetime import datetime
    import logging
    
    now = datetime.now()
    sleep_seconds = 60 - now.second - (now.microsecond / 1_000_000.0)

    if sleep_seconds < 60:
        logging.getLogger("System").info(f"⏳ กำลังหน่วงเวลา {sleep_seconds:.2f} วินาที เพื่อซิงค์รอบสแกนให้ตรงกับหน้าปัดนาฬิกา...")
        await asyncio.sleep(sleep_seconds)
        logging.getLogger("System").info("🎯 ซิงค์เวลาหน้าปัดนาฬิกาสำเร็จ! เริ่มสแกนตลาดได้!")

@trading_job.error
async def trading_job_error(exc):
    import traceback
    import logging
    error_msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logging.getLogger("System").error(f"🚨 [CRITICAL BUG] ระบบเทรดช็อตกระทันหัน:\n{error_msg}")

# ─────────────────────────────────────────────────────────────────────────────
# Fast tick loop (every 10s) — updates max_p / max_l only, no MT5 order calls.
# Lets the 1-min trailing SL react to intra-minute price peaks/troughs.
# ─────────────────────────────────────────────────────────────────────────────
@tasks.loop(seconds=10)
async def fast_tick_job():
    if not mt5.terminal_info():
        return
    positions = mt5.positions_get()
    if not positions:
        return
    for pos in positions:
        ticket = pos.ticket
        if ticket not in shared_state.ACTIVE_TRADE_TRACKER:
            continue
        current_p = pos.profit + pos.swap
        if current_p > shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"]:
            shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"] = current_p
        if current_p < shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"]:
            shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"] = current_p

@fast_tick_job.error
async def fast_tick_job_error(exc):
    import logging
    logging.getLogger("System").warning(f"⚠️ [fast_tick] error: {exc}")

def get_rsi(symbol):
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 100)
    if rates is None or len(rates) < 20: return None
    df = pd.DataFrame(rates)
    rsi_series = ta.rsi(df['close'], length=14)
    return rsi_series.iloc[-1]

def check_volatility(symbol, threshold):
    safe_threshold = max(threshold, 0.2)
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 2)
    if rates is None or len(rates) < 2: return False
    prev_close = rates[0]['close']
    curr_close = rates[1]['close']
    diff = abs(curr_close - prev_close) / prev_close * 100
    return diff > safe_threshold

def scaled_lot(base: float, mult: float, volume_min: float, volume_step: float) -> float:
    """Return base * mult rounded to volume_step and clamped to >= volume_min.
    Pure function — no MT5 calls. Extracted for unit-testability."""
    if mult == 1.0:
        return base
    step = volume_step if volume_step and volume_step > 0 else 0.01
    scaled = round((base * mult) / step) * step
    return max(volume_min, round(scaled, 2))


def guardian_m_should_close(fill_price, send_ref_price, point, max_slip):
    """Decide whether GUARDIAN-M should slip-close a just-filled order.

    Returns (should_close, slippage_pts).

    `send_ref_price` MUST be the live market price the order was actually sent at
    (a tick fetched immediately before order_send) — NOT a caller-supplied price
    captured earlier. place_order can run seconds after the caller snapped `price`
    (e.g. across `await ai.ai_analysis`); measuring against that stale snapshot turns
    decision-to-execution drift into phantom slippage and false-fires GUARDIAN-M,
    churning during volatile bursts (2026-06-06 BTC). Pure function — no MT5 calls."""
    if point <= 0:
        return (False, 0.0)
    slippage = abs(fill_price - send_ref_price) / point
    return (slippage > max_slip, slippage)


def place_order(symbol, type, price, rsi, comment, extra=None):
    lot = SYMBOLS_CONFIG[symbol]["lot"]
    # Optional position-size multiplier (e.g. reduced-size trend-sell rollout).
    # Wrapped so the default path (mult=1.0) is byte-for-byte unchanged.
    _mult = (extra or {}).get("lot_mult", 1.0)
    if _mult != 1.0:
        _si = mt5.symbol_info(symbol)
        if _si is not None:
            lot = scaled_lot(lot, _mult, _si.volume_min, _si.volume_step or 0.01)
        else:
            logging.getLogger(symbol).warning(f"⚠️ symbol_info None during lot_mult={_mult} — using full base lot {lot}")

    # Hard safety ceiling (config lot_max). No-op at base lot 0.01; matters once SP-B scales up.
    _lot_max = SYMBOLS_CONFIG[symbol].get("lot_max")
    if _lot_max is not None:
        lot = min(lot, _lot_max)

    raw_comment = str(comment).replace('\n', ' ').replace('\r', '').strip()
    safe_comment = raw_comment[:25]

    # Re-price off a fresh tick. place_order may run seconds after the caller snapped
    # `price` (e.g. across `await ai.ai_analysis`), so that snapshot can be stale. Pricing
    # the order — and the GUARDIAN-M slippage reference — off the live market at send time
    # stops decision-to-execution drift from masquerading as slippage (churn 2026-06-06).
    _fresh = mt5.symbol_info_tick(symbol)
    if _fresh is not None:
        price = _fresh.bid if type == "SELL" else _fresh.ask

    risk_level = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    base_pct = 0.001 
    sl_pct = base_pct * (1.0 + (3 - risk_level) * 0.2) 
    tp_pct = sl_pct * 1.0

    sl_dist = price * sl_pct
    tp_dist = price * tp_pct

    if type == "BUY":
        sl_price = price - sl_dist
        tp_price = price + tp_dist
    else:
        sl_price = price + sl_dist
        tp_price = price - tp_dist

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None: return False
    sl_price = round(sl_price, symbol_info.digits)
    tp_price = round(tp_price, symbol_info.digits)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if type == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl_price,      
        "tp": tp_price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": safe_comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    res = mt5.order_send(request)
    
    if res is None:
        err = mt5.last_error()
        logging.getLogger(symbol).error(f"❌ กระสุนด้าน! ส่งคำสั่งไม่ผ่าน Error Code: {err}")
        return False

    if res.retcode != mt5.TRADE_RETCODE_DONE:
        logging.getLogger(symbol).error(f"❌ MT5 ปฏิเสธคำสั่ง! สาเหตุ: {res.comment} (Code: {res.retcode})")
        return False

    # GUARDIAN-M slippage measured against the FRESH price the order was sent at
    # (re-priced above), not the caller's possibly-stale snapshot.
    max_slip = SYMBOLS_CONFIG.get(symbol, {}).get('max_slip', 300)
    should_close, slippage = guardian_m_should_close(res.price, price, symbol_info.point, max_slip)
    logging.getLogger(symbol).info(f"✅ {type} {symbol} {lot} lots at {res.price} (RSI: {rsi:.2f}) | {comment} | Slip: {slippage:.1f}pts")

    strat = ai.STRATEGY_DATA.get(symbol, {})
    macro_data = getattr(shared_state, 'MACRO_DATA', {}).get(symbol, {})
    m_bias = macro_data.get('bias', 'N/A')
    a_dir  = macro_data.get('allowed_direction', 'BOTH')
    ex     = extra or {}

    dbm.log_trade_entry(
        ticket=res.order, symbol=symbol, order_type=type, lot_size=lot,
        entry_price=res.price, entry_reason=comment, slippage=slippage,
        rsi_entry=rsi, market_regime=strat.get("regime", "N/A"),
        vol_thresh=strat.get("threshold", 0.0), risk_level=risk_level,
        macro_bias=m_bias, allowed_dir=a_dir,
        analyst_score=ex.get("analyst_score"),
        scout_score=ex.get("scout_score"),
        atr_pct_entry=strat.get("atr_pct"),
        spread_entry=ex.get("spread"),
        bb_pct_entry=strat.get("bb_pct"),
        h4_trend=macro_data.get("h4_trend"),
        d1_trend=macro_data.get("d1_trend"),
    )

    if should_close:
        logging.getLogger(symbol).warning(f"🛑 [GUARDIAN-M] Slip {slippage:.0f}pts > {max_slip} — ปิดออเดอร์ทันที")
        close_one_order(symbol=symbol, reason=f"GUARDIAN-M: slip {slippage:.0f}pts", ticket=res.order)
        # Stamp the slip-close for GUARDIAN-S cooldown so the next pyramid layer in this
        # scan (#2) and re-entry on the next ticks (#3) are blocked — stops the churn.
        # MUST use MT5 server time (tick.time) to match the gate's clock. If server time
        # is unavailable, skip stamping (fail-open) — never write a wall-clock value into
        # a server-time comparison, which could go negative and block entries for hours.
        _tk = mt5.symbol_info_tick(symbol)
        if _tk and _tk.time:
            if getattr(shared_state, 'SLIP_CLOSE_AT', None) is None:
                shared_state.SLIP_CLOSE_AT = {}
            shared_state.SLIP_CLOSE_AT[symbol] = int(_tk.time)
        return False

    shared_state.ACTIVE_TRADE_TRACKER[res.order] = {"max_p": 0.0, "max_l": 0.0}
    return True

def modify_position_sltp(ticket, symbol, new_sl, new_tp):
    request = {
        "action": mt5.TRADE_ACTION_SLTP, 
        "symbol": symbol,
        "position": ticket,              
        "sl": new_sl,                    
        "tp": new_tp                     
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"⚠️ [Error] แก้ไข SL/TP ไม่สำเร็จ (Ticket {ticket}): {result.comment}")
        return False
    print(f"🛡️ [Sniper] ล็อกกำไรสำเร็จ! ขยับ SL ไปที่ {new_sl} เรียบร้อย (Ticket {ticket})")
    return True

def close_one_order(symbol, reason="AI Action", ticket=None, max_float_p=0.0, max_float_l=0.0): 
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return False
    pos = next((p for p in positions if p.ticket == ticket), None)
    if pos is None: 
        return False
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return False
    
    current_profit = pos.profit + pos.swap
    
    type_dict = {mt5.ORDER_TYPE_BUY: mt5.ORDER_TYPE_SELL, mt5.ORDER_TYPE_SELL: mt5.ORDER_TYPE_BUY}
    price_dict = {mt5.ORDER_TYPE_BUY: tick.bid, mt5.ORDER_TYPE_SELL: tick.ask}
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": type_dict[pos.type],
        "position": pos.ticket,
        "price": price_dict[pos.type],
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "PCTrading Close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    res = mt5.order_send(request)
    if res is None: return False
        
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        logging.getLogger(symbol).info(f"💰 Order {pos.ticket} closed successfully")
        send_trade_notification(symbol, "CLOSE", res.price, 0, pos.ticket)
        
        tracker = shared_state.ACTIVE_TRADE_TRACKER.get(pos.ticket, {"max_p": 0.0, "max_l": 0.0})
        acc = mt5.account_info()
        curr_bal = acc.balance if acc else 0.0
        dbm.log_trade_exit(
            ticket=pos.ticket, exit_price=res.price, net_profit=current_profit, 
            max_float_p=tracker["max_p"], max_float_l=tracker["max_l"],
            exit_reason=reason, balance_after=curr_bal
        )
        if pos.ticket in shared_state.ACTIVE_TRADE_TRACKER:
            del shared_state.ACTIVE_TRADE_TRACKER[pos.ticket]
        return True
        
    return False

def check_daily_drawdown():
    current_day = datetime.now().day
    if getattr(shared_state, 'CURRENT_DAY', None) != current_day:
        shared_state.CURRENT_DAY = current_day
        shared_state.INITIAL_EQUITY = 0.0
        shared_state.IS_DAY_OFF = False
        logging.getLogger("System").info("🌅 เช้าวันใหม่! รีเซ็ตสถานะ Day-Off กลับมาลุยตลาดต่อ")

    if shared_state.IS_DAY_OFF:
        return True 

    account = mt5.account_info()
    if account is None: return False
    current_equity = account.equity

    if shared_state.INITIAL_EQUITY == 0.0:
        shared_state.INITIAL_EQUITY = current_equity
        logging.getLogger("System").info(f"🏦 บันทึกทุนเริ่มต้นของวันนี้: {current_equity:.2f} USD")
        return False

    if current_equity < shared_state.INITIAL_EQUITY:
        drawdown_usd = shared_state.INITIAL_EQUITY - current_equity
        drawdown_pct = (drawdown_usd / shared_state.INITIAL_EQUITY) * 100

        if drawdown_pct >= shared_state.MAX_DRAWDOWN_PERCENT:
            logging.getLogger("System").warning(f"🚨 [EQUITY ALERT] ขาดทุนสะสม {drawdown_pct:.2f}% (เกินลิมิต {shared_state.MAX_DRAWDOWN_PERCENT}%)")
            close_all_positions()
            shared_state.IS_DAY_OFF = True
            logging.getLogger("System").warning("🐢 บอทเข้าสู่ Day-Off Mode หยุดเทรดชั่วคราวจนกว่าจะถึงพรุ่งนี้!")
            return True
            
    return False

def close_all_positions():
    positions = mt5.positions_get()
    current_tickets = []
    if positions:
        for pos in positions:
            ticket = pos.ticket
            current_tickets.append(ticket)
            if ticket not in shared_state.ACTIVE_TRADE_TRACKER:
                shared_state.ACTIVE_TRADE_TRACKER[ticket] = {"max_p": 0.0, "max_l": 0.0}
        
    logging.getLogger("System").warning(f"🚨 [KILL SWITCH] กำลังกวาดล้างปิดออเดอร์ทั้งหมด {len(positions)} ไม้!")
    
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None: continue
        
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "magic": pos.magic,
            "comment": "DayOff Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        mt5.order_send(request)
    
    return True

def is_safe_trading_time(symbol):
    if "BTC" in symbol.upper():
        return True 
        
    from datetime import datetime, timedelta, time
    
    thai_time = datetime.utcnow() + timedelta(hours=7)
    weekday = thai_time.weekday()
    current_time = thai_time.time()

    if weekday == 5 and current_time > time(3, 30):
        return False
        
    if weekday == 6:
        return False

    if weekday == 0 and current_time < time(8, 0):
        if current_time.minute % 30 == 0 and current_time.second < 10:
            logging.getLogger(symbol).warning(f"⏳ ตลาดเพิ่งเปิดวันจันทร์ บอทรอให้กราฟนิ่ง 2 ชั่วโมง (เริ่ม 08:00 น.)")
        return False
        
    return True
