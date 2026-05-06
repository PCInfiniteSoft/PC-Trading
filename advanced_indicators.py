import MetaTrader5 as mt5
import pandas as pd

def calculate_rsi(prices, period=14):
    """คำนวณค่า RSI ปัจจุบัน"""
    if len(prices) < period:
        return 50.0
    df = pd.DataFrame(prices, columns=['close'])
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.fillna(100).iloc[-1], 2)

def get_3_indicators(symbol, timeframe=mt5.TIMEFRAME_M15):
    # ==========================================
    # 📊 1. ดึงข้อมูลไทม์เฟรมปัจจุบัน (M15)
    # ==========================================
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 50)
    if rates is None or len(rates) < 50:
        return {"supertrend": "N/A", "supertrend_h1": "N/A", "ob_zone": "N/A", "long_stop": 0, "short_stop": 0}
    
    df = pd.DataFrame(rates)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift())
    df['tr2'] = abs(df['low'] - df['close'].shift())
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    
    current_atr = df['atr'].iloc[-1]
    current_close = df['close'].iloc[-1]
    
    # วิชาที่ 1: Chandelier Exit (จุดหนีตาย M15)
    highest_22 = df['high'].rolling(22).max().iloc[-1]
    lowest_22 = df['low'].rolling(22).min().iloc[-1]
    long_stop = highest_22 - (current_atr * 3)
    short_stop = lowest_22 + (current_atr * 3)

    # วิชาที่ 2: Supertrend (เทรนด์ M15)
    st_dir = "UPTREND 🟢" if current_close > long_stop else "DOWNTREND 🔴"

    # วิชาที่ 3: SMC Order Block (M15)
    df['body'] = abs(df['close'] - df['open'])
    big_candles = df[df['body'] > (df['atr'] * 1.5)]
    ob_zone = "No Zone"
    if not big_candles.empty:
        last_big = big_candles.iloc[-1]
        if last_big['close'] > last_big['open']:
            ob_zone = f"Demand Zone {last_big['low']:.2f} - {last_big['open']:.2f}"
        else:
            ob_zone = f"Supply Zone {last_big['close']:.2f} - {last_big['high']:.2f}"

    # ==========================================
    # 👑 2. ดึงข้อมูลพี่ใหญ่ ไทม์เฟรม 1 ชั่วโมง (H1)
    # ==========================================
    rates_h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 50)
    st_dir_h1 = "N/A"
    
    if rates_h1 is not None and len(rates_h1) >= 50:
        df_h1 = pd.DataFrame(rates_h1)
        df_h1['tr0'] = abs(df_h1['high'] - df_h1['low'])
        df_h1['tr1'] = abs(df_h1['high'] - df_h1['close'].shift())
        df_h1['tr2'] = abs(df_h1['low'] - df_h1['close'].shift())
        df_h1['tr'] = df_h1[['tr0', 'tr1', 'tr2']].max(axis=1)
        df_h1['atr'] = df_h1['tr'].rolling(14).mean()
        
        curr_atr_h1 = df_h1['atr'].iloc[-1]
        curr_close_h1 = df_h1['close'].iloc[-1]
        
        high_22_h1 = df_h1['high'].rolling(22).max().iloc[-1]
        long_stop_h1 = high_22_h1 - (curr_atr_h1 * 3)
        
        st_dir_h1 = "UPTREND 🟢" if curr_close_h1 > long_stop_h1 else "DOWNTREND 🔴"

    # ส่งค่าทั้งหมดกลับไปให้ AI ตัดสินใจ
    return {
        "supertrend": st_dir,           # เทรนด์ M15
        "supertrend_h1": st_dir_h1,     # เทรนด์ H1 (ตัวคุมเกม)
        "ob_zone": ob_zone,             # โซนเด้ง
        "long_stop": round(long_stop, 2),
        "short_stop": round(short_stop, 2)
    }

def get_macro_trends(symbol):
    """
    อ่านค่ากราฟ H4 และ D1 เพื่อหาแนวโน้มหลักของตลาด
    """
    trends = {"h4_trend": "SIDEWAY", "d1_trend": "SIDEWAY"}
    
    # วนลูปดึงข้อมูลทั้ง 2 ไทม์เฟรม
    timeframes = [
        (mt5.TIMEFRAME_H4, "h4_trend"),
        (mt5.TIMEFRAME_D1, "d1_trend")
    ]
    
    for tf, key in timeframes:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, 50)
        
        if rates is not None and len(rates) >= 50:
            df = pd.DataFrame(rates)
            
            # คำนวณ ATR เพื่อใช้วัดความผันผวน
            df['tr0'] = abs(df['high'] - df['low'])
            df['tr1'] = abs(df['high'] - df['close'].shift())
            df['tr2'] = abs(df['low'] - df['close'].shift())
            df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
            df['atr'] = df['tr'].rolling(14).mean()
            
            curr_atr = df['atr'].iloc[-1]
            curr_close = df['close'].iloc[-1]
            
            # คำนวณ Chandelier Exit (สูตรเดียวกับ M15 ของพี่)
            high_22 = df['high'].rolling(22).max().iloc[-1]
            long_stop = high_22 - (curr_atr * 3)
            
            # ตัดสินเทรนด์
            trends[key] = "UPTREND 🟢" if curr_close > long_stop else "DOWNTREND 🔴"
            
    return trends

def get_trend_confirmation(symbol, order_type: str, timeframe=mt5.TIMEFRAME_M15) -> dict:
    """
    [Fix #3] ตรวจสอบ momentum confirmation ก่อนยิงออเดอร์
    ใช้ MACD crossover + EMA alignment เพื่อกรอง false signal จาก RSI
    
    คืนค่า dict:
        confirmed (bool)  — True ถ้าทิศทางสอดคล้อง
        macd_signal (str) — "BULLISH" / "BEARISH" / "NEUTRAL"
        ema_aligned (bool) — True ถ้า EMA20 > EMA50 (BUY) หรือ EMA20 < EMA50 (SELL)
        reason (str)      — คำอธิบายสั้น ๆ สำหรับ log
    """
    result = {"confirmed": False, "macd_signal": "NEUTRAL", "ema_aligned": False, "reason": "Insufficient data"}

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)
    if rates is None or len(rates) < 60:
        return result

    df = pd.DataFrame(rates)
    close = df['close']

    # --- MACD (12, 26, 9) ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    macd_now = macd_line.iloc[-1]
    macd_prev = macd_line.iloc[-2]
    sig_now = signal_line.iloc[-1]
    sig_prev = signal_line.iloc[-2]

    # Crossover detection
    bullish_cross = (macd_prev < sig_prev) and (macd_now >= sig_now)
    bearish_cross = (macd_prev > sig_prev) and (macd_now <= sig_now)
    macd_above = macd_now > sig_now
    macd_below = macd_now < sig_now

    if bullish_cross or macd_above:
        macd_signal = "BULLISH"
    elif bearish_cross or macd_below:
        macd_signal = "BEARISH"
    else:
        macd_signal = "NEUTRAL"

    # --- EMA Alignment (20 / 50) ---
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema_aligned_buy  = ema20 > ema50
    ema_aligned_sell = ema20 < ema50

    order = order_type.upper()

    if order == "BUY":
        ema_aligned = ema_aligned_buy
        confirmed = (macd_signal == "BULLISH") and ema_aligned
        reason = f"MACD={macd_signal} | EMA20({'>' if ema_aligned_buy else '<'}EMA50)"
    elif order == "SELL":
        ema_aligned = ema_aligned_sell
        confirmed = (macd_signal == "BEARISH") and ema_aligned
        reason = f"MACD={macd_signal} | EMA20({'<' if ema_aligned_sell else '>'}EMA50)"
    else:
        ema_aligned = False
        confirmed = False
        reason = f"Unknown order type: {order_type}"

    return {
        "confirmed": confirmed,
        "macd_signal": macd_signal,
        "ema_aligned": ema_aligned,
        "reason": reason
    }
