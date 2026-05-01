import MetaTrader5 as mt5
import pandas as pd

def get_3_indicators(symbol, timeframe=mt5.TIMEFRAME_M15):
    # 1. ดึงกราฟย้อนหลังมา 50 แท่งเพื่อคำนวณ
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 50)
    if rates is None or len(rates) < 50:
        return {"supertrend": "N/A", "ob_zone": "N/A", "long_stop": 0, "short_stop": 0}
    
    df = pd.DataFrame(rates)
    
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift())
    df['tr2'] = abs(df['low'] - df['close'].shift())
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    
    current_atr = df['atr'].iloc[-1]
    current_close = df['close'].iloc[-1]
    
    # ---------------------------------------------
    # 🔴 วิชาที่ 1: Chandelier Exit (จุดหนีตาย)
    # ---------------------------------------------
    highest_22 = df['high'].rolling(22).max().iloc[-1]
    lowest_22 = df['low'].rolling(22).min().iloc[-1]
    
    long_stop = highest_22 - (current_atr * 3)
    short_stop = lowest_22 + (current_atr * 3)

    # ---------------------------------------------
    # 🔴 วิชาที่ 2: Supertrend (เทรนด์ใหญ่)
    # ---------------------------------------------
    st_dir = "UPTREND 🟢" if current_close > long_stop else "DOWNTREND 🔴"

    # ---------------------------------------------
    # 🔴 วิชาที่ 3: SMC Order Block (โซนรายใหญ่)
    # ---------------------------------------------
    df['body'] = abs(df['close'] - df['open'])
    # หาแท่งที่มีขนาดเนื้อเทียนใหญ่กว่าปกติ 1.5 เท่า (แท่งที่สถาบันอัดเงิน)
    big_candles = df[df['body'] > (df['atr'] * 1.5)]
    
    ob_zone = "No Zone"
    if not big_candles.empty:
        last_big = big_candles.iloc[-1]
        if last_big['close'] > last_big['open']: # ทุบซื้อ (Demand Zone)
            ob_zone = f"Demand Zone {last_big['low']:.2f} - {last_big['open']:.2f}"
        else: # ทุบขาย (Supply Zone)
            ob_zone = f"Supply Zone {last_big['close']:.2f} - {last_big['high']:.2f}"
            
    return {
        "supertrend": st_dir,
        "ob_zone": ob_zone,
        "long_stop": round(long_stop, 2),
        "short_stop": round(short_stop, 2)
    }

def calculate_rsi(prices, period=14):
    """
    ฟังก์ชันคำนวณค่า RSI รับค่า prices (list ของราคาปิด) และส่งคืนค่า RSI ปัจจุบัน
    """
    # ถ้าข้อมูลน้อยเกินไป ให้ส่งค่ากลางๆ กลับไปก่อน
    if len(prices) < period:
        return 50.0
    
    # ใช้ pandas คำนวณ (เพราะเรา import pandas ไว้ด้านบนแล้ว)
    df = pd.DataFrame(prices, columns=['close'])
    delta = df['close'].diff()
    
    # แยกส่วนที่เป็นกำไร (ขึ้น) และขาดทุน (ลง)
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    
    # คำนวณค่าเฉลี่ยแบบ Exponential (สูตรดั้งเดิมของ J. Welles Wilder)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    
    rs = ema_up / ema_down
    rsi = 100 - (100 / (1 + rs))
    
    # กรณีที่กราฟขึ้นรวดเดียวจน ema_down เป็น 0 ค่า rsi จะเป็น NaN ให้เติม 100 แทน
    rsi = rsi.fillna(100)
    
    return round(rsi.iloc[-1], 2)