import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import logging
import shared_state
from datetime import datetime
from bot_config import *
from trade_noti import send_trade_notification

def get_rsi(symbol):
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 100)
    if rates is None or len(rates) < 20: return None
    df = pd.DataFrame(rates)
    rsi_series = ta.rsi(df['close'], length=14)
    return rsi_series.iloc[-1]

def check_volatility(symbol, threshold):
    # 🟢 ตรวจสอบแรงกระชากเทียบกับค่า Threshold ที่ AI ให้มา
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 2)
    if rates is None or len(rates) < 2: return False
    prev_close = rates[0]['close']
    curr_close = rates[1]['close']
    diff = abs(curr_close - prev_close) / prev_close * 100
    return diff > threshold

def place_order(symbol, type, price, rsi, comment):
    lot = SYMBOLS_CONFIG[symbol]["lot"]

    raw_comment = str(comment).replace('\n', ' ').replace('\r', '').strip()
    safe_comment = raw_comment[:25]

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if type == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "magic": MAGIC_NUMBER,
        "comment": safe_comment,  # 🟢 2. จุดสำคัญ! ต้องเปลี่ยนมาใช้ safe_comment ตรงนี้ครับ
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    res = mt5.order_send(request)
    
    # 🟢 1. ดักจับกรณี MT5 ปฏิเสธการส่งออเดอร์ (คืนค่า None)
    if res is None:
        error_code = mt5.last_error()
        logging.getLogger(symbol).error(f"❌ {type} Failed! คืนค่า None (MT5 Error: {error_code}) - โปรดเช็คปุ่ม Algo Trading หรือเปิดคู่เงินใน Market Watch")
        return False
        
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        logging.getLogger(symbol).info(f"✅ {type} {symbol} {lot} lots at {price} (RSI: {rsi:.2f}) | {comment}")
        send_trade_notification(symbol, type, price, rsi, res.order)
        return True
        
    logging.getLogger(symbol).error(f"❌ {type} Error: {res.comment}")
    return False

def close_one_order(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return False
    pos = positions[0] # ปิดไม้ที่เปิดก่อน (FIFO)
    tick = mt5.symbol_info_tick(symbol)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "position": pos.ticket,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
        "magic": MAGIC_NUMBER,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    res = mt5.order_send(request)
    
    # 🟢 2. ดักจับกรณี MT5 ปฏิเสธการปิดออเดอร์ (คืนค่า None)
    if res is None:
        error_code = mt5.last_error()
        logging.getLogger(symbol).error(f"❌ Close Order Failed! (MT5 Error: {error_code})")
        return False
        
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        # 🟢 คงภาษาอังกฤษไว้ตามที่พี่ตั้งค่าไว้เมื่อวานครับ
        logging.getLogger(symbol).info(f"💰 Order {pos.ticket} closed successfully")
        send_trade_notification(symbol, "CLOSE", res.price, 0, pos.ticket)
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
    if positions is None or len(positions) == 0:
        return True # ไม่มีออเดอร์ให้ปิด ถือว่าเคลียร์แล้ว
        
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