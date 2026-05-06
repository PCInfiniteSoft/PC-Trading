import sqlite3
import MetaTrader5 as mt5
from datetime import datetime, timedelta

class RiskManager:
    def __init__(self, db_path="trading_history.db"):
        self.db_path = db_path

    def is_cooldown_active(self, cooldown_minutes=5):
        """
        [Agent 3] เช็คว่าบอทเพิ่งปิดออเดอร์ (หรือโดน SL) ไปไม่นานหรือไม่
        ถ้ายังอยู่ในช่วง Cooldown จะ return True (ห้ามเทรด)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # ดึงเวลาที่ปิดออเดอร์ล่าสุดจากตาราง trade_history
            cursor.execute("""
                SELECT exit_time 
                FROM trade_history 
                WHERE exit_time IS NOT NULL 
                ORDER BY exit_time DESC LIMIT 1
            """)
            result = cursor.fetchone()
            conn.close()

            if result and result[0]:
                last_exit_str = result[0]
                # แปลง String จาก DB เป็น Datetime
                # สมมติฐานว่า format ใน DB คือ 'YYYY-MM-DD HH:MM:SS'
                last_exit_time = datetime.strptime(last_exit_str, "%Y-%m-%d %H:%M:%S")
                current_time = datetime.now()
                
                # คำนวณระยะเวลาที่ผ่านไป
                time_passed = current_time - last_exit_time
                
                if time_passed < timedelta(minutes=cooldown_minutes):
                    minutes_left = cooldown_minutes - (time_passed.total_seconds() / 60)
                    print(f"🛑 [Risk Manager] เตะปลั๊ก! ระบบติด Cooldown ต้องพักอีก {minutes_left:.1f} นาที")
                    return True # ติด Cooldown อยู่ ห้ามลั่นไก!
            
            return False # พ้น Cooldown แล้ว ปลอดภัย!

        except Exception as e:
            print(f"⚠️ [Error] RiskManager.is_cooldown_active: {e}")
            # กรณีหา DB ไม่เจอ หรือเพิ่งรันครั้งแรก ให้ถือว่าไม่ติด Cooldown
            return False
        
    def is_against_trend(self, current_h1_trend, order_type):
        """
        [Agent 3] กฎเหล็กล็อกเทรนด์ (Hard Block)
        เช็คว่าคำสั่งซื้อขายที่กำลังจะส่ง สวนทางกับเทรนด์หลัก (H1) หรือไม่
        """
        # ทำให้เป็นตัวพิมพ์ใหญ่ทั้งหมดเพื่อป้องกัน error เรื่องตัวพิมพ์เล็ก/ใหญ่
        trend = str(current_h1_trend).upper()
        order = str(order_type).upper()

        # ถ้าเทรนด์ H1 เป็นขาขึ้น (UPTREND) ห้ามยิง SELL (Short) เด็ดขาด
        if "UP" in trend and order == "SELL":
            print("🛑 [Risk Manager] เตะปลั๊ก! ห้ามเปิด Short (SELL) สวนเทรนด์ขาขึ้นเด็ดขาด!")
            return True # สวนเทรนด์ = ห้ามเทรด (True คือติดบล็อก)
            
        # ถ้าเทรนด์ H1 เป็นขาลง (DOWNTREND) ห้ามยิง BUY (Long) เด็ดขาด
        elif "DOWN" in trend and order == "BUY":
            print("🛑 [Risk Manager] เตะปลั๊ก! ห้ามเปิด Long (BUY) สวนเทรนด์ขาลงเด็ดขาด!")
            return True # สวนเทรนด์ = ห้ามเทรด (True คือติดบล็อก)
            
        print(f"✅ [Risk Manager] ทิศทางปลอดภัย (Trend: {trend} | Order: {order})")
        return False # ไม่สวนเทรนด์ = ปลอดภัย ยิงได้!
    
    def is_spread_too_high(self, symbol, ai_recommended_spread):
        """
        [Agent 3] ระบบกรองสเปรด (Dynamic Spread Filter)
        เช็คค่า Spread ปัจจุบันว่ากว้างเกินกว่าที่รับได้หรือไม่
        """
        try:
            # 1. ดึงข้อมูลล่าสุดจาก MT5
            symbol_info = mt5.symbol_info(symbol)
            
            if symbol_info is None:
                print(f"⚠️ [Risk Manager] หาข้อมูลสเปรดของ {symbol} ไม่เจอ รบกวนเช็คชื่อ Symbol ครับ")
                return True # บล็อกไว้ก่อนเพื่อความปลอดภัย
            
            # 2. เตรียมข้อมูล Spread และเพดาน (Threshold)
            current_spread = symbol_info.spread
            
            # 🧠 ใช้ค่าที่ AI แนะนำมา ถ้า AI ไม่ส่งมา (None/False) ให้ใช้ 60 เป็นค่ามาตรฐาน
            threshold = ai_recommended_spread if ai_recommended_spread else 60

            # 3. ตรวจสอบเงื่อนไข (ใช้ชื่อตัวแปร threshold ให้ตรงกัน)
            if current_spread > threshold:
                print(f"🛑 [Risk Manager] เตะปลั๊ก! สเปรดถ่างเกินไป (Current: {current_spread} > AI Limit: {threshold})")
                return True # สเปรดถ่าง = ติดบล็อก ห้ามยิง
                
            print(f"✅ [Risk Manager] สเปรดอยู่ในเกณฑ์ปกติ (Spread: {current_spread})")
            return False # ผ่าน! ยิงได้

        except Exception as e:
            # 🛡️ เข็มขัดนิรภัย: ถ้ามี Error อะไรก็ตาม ให้บล็อกการเทรดทันที
            print(f"⚠️ [Error] RiskManager.is_spread_too_high: {e}")
            return True