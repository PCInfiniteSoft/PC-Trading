import logging
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

@tasks.loop(minutes=1)
async def trading_job():

    from discord_manager import get_win_loss_text 
    
    shared_state.SCAN_COUNT += 1

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

    if not mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV): 
        if not getattr(shared_state, 'IS_MT5_DOWN', False):
            shared_state.MT5_DISCONNECT_COUNT = getattr(shared_state, 'MT5_DISCONNECT_COUNT', 0) + 1
            shared_state.IS_MT5_DOWN = True
        return
    else:
        shared_state.IS_MT5_DOWN = False
    
    now = datetime.now()
    now_time = now.time()
    
    is_news_window = False
    for nt in getattr(shared_state, 'TODAY_NEWS_TIMES', []):
        news_dt = datetime.combine(now.date(), nt)
        start_window = (news_dt - timedelta(minutes=5)).time()
        end_window = (news_dt + timedelta(minutes=15)).time()
        
        if start_window <= now_time <= end_window:
            is_news_window = True
            break
    if is_news_window and shared_state.CURRENT_LOOP_MINS != 1:
        shared_state.CURRENT_LOOP_MINS = 1
        logging.getLogger("System").warning("🚨 [HYPER-ACTIVE MODE] เข้าสู่ช่วงข่าวกล่องแดง! AI สแกนทุก 1 นาที")
    elif not is_news_window and shared_state.CURRENT_LOOP_MINS == 1:
        shared_state.CURRENT_LOOP_MINS = 5
        logging.getLogger("System").info("✅ [NORMAL MODE] พ้นช่วงข่าวกล่องแดง ปรับรอบสแกนกลับเป็น 5 นาที")

    if shared_state.CURRENT_LOOP_MINS == 1:
        is_ai_update_turn = True
    else:
        is_ai_update_turn = (now.minute % 5 == 0)     
    
    if is_ai_update_turn and not ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = getattr(shared_state, 'AI_RETRY_COUNT', 0) + 1
        logging.getLogger("System").warning(f"⚠️ พยายามปลุก AI ให้ตื่น (รอบที่ {shared_state.AI_RETRY_COUNT}/5)...")
    elif ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = 0 

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
                shared_state.TRADE_LAYERS[s] = {"buy": [False]*5, "sell": [False]*5}

        strat = ai.STRATEGY_DATA[s]

        if is_ai_update_turn:
            buy_target = strat['buy'][0] if strat.get('buy') else '--'
            logging.getLogger(s).info(f"📊 RSI: {rsi:.2f} | เป้าซื้อ AI: < {buy_target} | ตลาด: {strat['regime']}")
        
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

        # 🎯 2. ลูปยิงออเดอร์ (รองรับทั้ง 2 ฝั่ง)
        for i in range(5):
            
            # ==========================================
            # 📉 ฝั่งขาลง: RSI ลงมาโซน Oversold (หาจังหวะ BUY หรือ ปิด SELL)
            # ==========================================
            if rsi < strat['buy'][i]:
                # ก. ปิดทำกำไรไม้ SELL ที่ถืออยู่ (เพราะกราฟลงมาถึงเป้าแล้ว)
                if has_sell:
                    for t in sell_tickets:
                        close_one_order(symbol=s, reason="RSI Hit Buy Target (TP) 🎯", ticket=t)
                    has_sell = False # ปิดเสร็จอัปเดตสถานะพอร์ต
                    
                # ข. เปิดไม้ BUY สวนขึ้นไป (ส่งให้ AI ยืนยัน)
                if not shared_state.TRADE_LAYERS.get(s, {}).get("buy", [False]*5)[i]:
                    if ai.AI_IS_ONLINE:
                        tick = mt5.symbol_info_tick(s)
                        if tick is not None:
                            st_data = adv.get_3_indicators(s) 
                            ans = await ai.ai_analysis(s, tick.ask, rsi, st_data) # ขา Buy ใช้ tick.ask
                            
                            if ans.get('decision') == "BUY":
                                if place_order(s, "BUY", tick.ask, rsi, f"L{i+1}:{ans.get('reason', '')[:18]}"): 
                                    shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["buy"][i] = True
                                    has_buy = True

            # ==========================================
            # 📈 ฝั่งขาขึ้น: RSI พุ่งขึ้นโซน Overbought (หาจังหวะ SELL หรือ ปิด BUY)
            # ==========================================
            if rsi > strat['sell'][i]:
                # ก. ปิดทำกำไรไม้ BUY ที่ถืออยู่ (เพราะกราฟพุ่งขึ้นมาชนเป้าแล้ว)
                if has_buy:
                    for t in buy_tickets:
                        close_one_order(symbol=s, reason="RSI Hit Sell Target (TP) 🎯", ticket=t)
                    has_buy = False # ปิดเสร็จอัปเดตสถานะพอร์ต

                # ข. เปิดไม้ SELL สวนลงมา (ส่งให้ AI ยืนยัน)
                if not shared_state.TRADE_LAYERS.get(s, {}).get("sell", [False]*5)[i]:
                    if ai.AI_IS_ONLINE:
                        tick = mt5.symbol_info_tick(s)
                        if tick is not None:
                            st_data = adv.get_3_indicators(s) 
                            ans = await ai.ai_analysis(s, tick.bid, rsi, st_data) # ขา Sell ต้องใช้ tick.bid
                            
                            if ans.get('decision') == "SELL":
                                if place_order(s, "SELL", tick.bid, rsi, f"L{i+1}:{ans.get('reason', '')[:18]}"): 
                                    shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True
                                    has_sell = True

            if mt5.positions_get(symbol=s) and rsi > strat['sell'][i] and not shared_state.TRADE_LAYERS.get(s, {}).get("sell", [False]*5)[i]:
                if close_one_order(s): 
                    shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True

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
                
            # 1.1 อัปเดตจุดสูงสุด/ต่ำสุดที่เคยทำได้
            if current_p > shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"]:
                shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"] = current_p
                
            if current_p < shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"]:
                shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_l"] = current_p

            # ==========================================
            # 🏃‍♂️💨 1.2 ระบบ Trailing Profit & Break-Even (ควบคุมโดย AI 100%)
            # ==========================================
            max_profit = shared_state.ACTIVE_TRADE_TRACKER[ticket]["max_p"]
            
            # 🤖 ดึงกลยุทธ์จากสมอง AI ประจำคู่เงินนั้นๆ (ซึ่งจะถูกอัปเดตใหม่ทุก 5 นาที)
            strat = ai.STRATEGY_DATA.get(symbol, {})
            
            # ⚙️ รับค่า Dynamic จาก AI (ถ้าช่วงไหน AI เออเร่อหรือตอบไม่ครบ จะใช้ค่า Default ด้านหลังสุดกันเหนียวไว้)
            TP_ACTIVATION = strat.get("tp_activation", 3.0)     
            PULLBACK_PCT = strat.get("pullback_pct", 0.30)      
            BE_ACTIVATION = strat.get("be_activation", 1.50)    
            BE_LOCK_PROFIT = strat.get("be_lock_profit", 0.20)  

            # 🛡️ เกราะชั้นใน: เช็ค Trailing Profit (กำไรทะลุเป้าที่ AI วางไว้ แล้วย่อตัว)
            if max_profit >= TP_ACTIVATION:
                lock_profit_line = max_profit * (1.0 - PULLBACK_PCT)
                if current_p <= lock_profit_line:
                    logging.getLogger(symbol).warning(f"🏃‍♂️💨 AI Trailing Profit ทำงาน! (Max: +{max_profit:.2f}$ ย่อเหลือ +{current_p:.2f}$) ตัดจบ!")
                    close_one_order(symbol, reason="AI Trailing Profit 🏃‍♂️💨", ticket=ticket)
                    continue # ปิดแล้วให้ข้ามไปเช็คไม้ถัดไปเลย
                    
            # 🛡️ เกราะชั้นนอก: เช็ค Break-Even (วิ่งไปถึงเป้าแรกของ AI แล้วโดนทุบกลับ)
            elif max_profit >= BE_ACTIVATION:
                if current_p <= BE_LOCK_PROFIT:
                    logging.getLogger(symbol).warning(f"🛡️ AI Break-Even บังทุนทำงาน! (Max: +{max_profit:.2f}$ ร่วงมาเหลือ +{current_p:.2f}$) ปิดเจ๊า!")
                    close_one_order(symbol, reason="AI Break-Even 🛡️", ticket=ticket)

    # ==========================================
    # 🟢 2. ระบบตามเก็บตกไม้ที่ถูกโบรคเกอร์ปิด (ชน SL/TP)
    # ==========================================
    closed_tickets = []
    for ticket in list(shared_state.ACTIVE_TRADE_TRACKER.keys()):
        if ticket not in current_tickets:
            closed_tickets.append(ticket)
            
    for ticket in closed_tickets:
        now = datetime.now()
        start_of_day = datetime(now.year, now.month, now.day)
        deals = mt5.history_deals_get(start_of_day, now, group="*")
        
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
    """ 🟢 ฟังก์ชันนี้จะทำงานแค่ครั้งเดียวตอนกด Start เพื่อหน่วงเวลาให้ตรงกับวินาทีที่ 00 """
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

def place_order(symbol, type, price, rsi, comment):
    lot = SYMBOLS_CONFIG[symbol]["lot"]

    raw_comment = str(comment).replace('\n', ' ').replace('\r', '').strip()
    safe_comment = raw_comment[:25]

    # 🟢 [ระบบใหม่] คำนวณ SL / TP แบบ Dynamic (% จากราคาปัจจุบัน)
    # ป้องกันการตั้ง SL แคบเกินไปจนโดน Spread / ข่าว สะบัดกินฟรี
    risk_level = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    
    # ฐานความกว้าง = 0.1% ของราคา (เช่น ทอง 4500 = ห่าง 4.5$, BTC 75000 = ห่าง 75$)
    base_pct = 0.001 
    
    # ยืดหยุ่นตาม Risk (Risk 1-2 ทนลากได้เยอะ SL กว้าง / Risk 4-5 เจ็บสั้น SL แคบลง)
    sl_pct = base_pct * (1.0 + (3 - risk_level) * 0.2) 
    tp_pct = sl_pct * 1.5 # ตั้ง TP ให้กว้างกว่า SL 1.5 เท่า (Risk:Reward = 1:1.5)
    
    sl_dist = price * sl_pct
    tp_dist = price * tp_pct

    if type == "BUY":
        sl_price = price - sl_dist
        tp_price = price + tp_dist
    else: # SELL
        sl_price = price + sl_dist
        tp_price = price - tp_dist

    # ปรับทศนิยมให้ถูกต้องตามมาตรฐานของโบรคเกอร์
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
        "sl": sl_price, # 🛡️ ส่ง SL แบบใหม่ไปตั้งค่าที่โบรกเกอร์
        "tp": tp_price, # 🎯 ส่ง TP แบบใหม่ไปตั้งค่าที่โบรกเกอร์
        "magic": MAGIC_NUMBER,
        "comment": safe_comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    res = mt5.order_send(request)
    
    if res is None:
        error_code = mt5.last_error()
        logging.getLogger(symbol).error(f"❌ {type} Failed! คืนค่า None (MT5 Error: {error_code})")
        return False
        
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        slippage = abs(res.price - price) / mt5.symbol_info(symbol).point
        logging.getLogger(symbol).info(f"✅ {type} {symbol} {lot} lots at {price} (RSI: {rsi:.2f}) | {comment}")
        send_trade_notification(symbol, type, price, rsi, res.order)
        strat = ai.STRATEGY_DATA.get(symbol, {})
        dbm.log_trade_entry(
            ticket=res.order, symbol=symbol, order_type=type, lot_size=lot, 
            entry_price=res.price, entry_reason=comment, slippage=slippage,
            rsi_entry=rsi, market_regime=strat.get("regime", "N/A"),
            vol_thresh=strat.get("threshold", 0.0), risk_level=risk_level
        )
        shared_state.ACTIVE_TRADE_TRACKER[res.order] = {"max_p": 0.0, "max_l": 0.0}
        return True

    logging.getLogger(symbol).error(f"❌ {type} Error: {res.comment}")
    return False

def close_one_order(symbol, reason="AI Action", max_float_p=0.0, max_float_l=0.0): 
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return False
    pos = next((p for p in positions if p.ticket == ticket), positions[0])
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
            max_float_p=tracker["max_p"], max_float_l=tracker["max_l"], # 🟢 2. ดึงค่าจาก tracker มาใส่ตรงนี้
            exit_reason=reason, balance_after=curr_bal
        )

        if pos.ticket in shared_state.ACTIVE_TRADE_TRACKER:
            del shared_state.ACTIVE_TRADE_TRACKER[pos.ticket]
            
        return True
        
    return False

def check_daily_drawdown():
    """ 📊 ฟังก์ชันเช็คยอดเงินและคำนวณ % ขาดทุนรายวัน """

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
    """ 🚨 ฟังก์ชันล้างพอร์ต ปิดทุกออเดอร์ที่ค้างอยู่ทันที """
    import MetaTrader5 as mt5
    import logging
    
    positions = mt5.positions_get()
    current_tickets = []
    if positions:
        for pos in positions:
            ticket = pos.ticket
            current_tickets.append(ticket) # 🟢 เก็บตั๋วใส่กระเป๋า
            
            if ticket not in shared_state.ACTIVE_TRADE_TRACKER:
                shared_state.ACTIVE_TRADE_TRACKER[ticket] = {"max_p": 0.0, "max_l": 0.0}
        
    logging.getLogger("System").warning(f"🚨 [KILL SWITCH] กำลังกวาดล้างปิดออเดอร์ทั้งหมด {len(positions)} ไม้!")
    
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None: continue
        
        # ถ้าถือ BUY ให้สั่ง SELL, ถ้าถือ SELL ให้สั่ง BUY
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
    import logging
    
    thai_time = datetime.utcnow() + timedelta(hours=7)
    weekday = thai_time.weekday() # 0 = จันทร์, 5 = เสาร์, 6 = อาทิตย์
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