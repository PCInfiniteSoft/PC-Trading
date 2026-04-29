import sqlite3
import logging
from datetime import datetime

DB_NAME = "trading_history.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # 1. ตารางประวัติการเทรด
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_history (
                ticket INTEGER PRIMARY KEY,
                symbol TEXT,
                order_type TEXT,
                lot_size REAL,
                entry_time TEXT,
                entry_price REAL,
                entry_reason TEXT,
                slippage REAL,
                exit_time TEXT,
                exit_price REAL,
                net_profit REAL,
                max_floating_profit REAL,
                max_floating_loss REAL,
                exit_reason TEXT,
                balance_after_trade REAL
            )
        ''')
        
        # 2. ตารางบริบทสภาพตลาดและ AI
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_context (
                ticket INTEGER PRIMARY KEY,
                rsi_entry REAL,
                market_regime TEXT,
                volatility_threshold REAL,
                risk_level INTEGER
            )
        ''')
        
        # 3. ตารางประวัติข่าวเศรษฐกิจ
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news_history (
                news_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                time TEXT,
                currency TEXT,
                impact TEXT,
                title TEXT
            )
        ''')
        
        # 4. ตารางสรุปผลงานรายวัน (Dashboard)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_performance (
                date TEXT PRIMARY KEY,
                start_balance REAL,
                end_balance REAL,
                total_profit REAL,
                max_drawdown_pct REAL,
                total_trades INTEGER,
                win_trades INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.getLogger("System").error(f"❌ DB Init Error: {e}")

# สร้างตารางทันทีที่โปรแกรมถูกรัน
init_db()

# ==========================================
# 🟢 ฟังก์ชันบันทึกข้อมูล (นำไปใช้ในไฟล์อื่น)
# ==========================================

def log_trade_entry(ticket, symbol, order_type, lot_size, entry_price, rsi_entry, market_regime, vol_thresh, risk_level, entry_reason, slippage):
    """ บันทึกตอนเปิดไม้ (เข้า 2 ตารางพร้อมกัน) """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # ลงตาราง 1
        cursor.execute('''
           INSERT INTO trade_history (ticket, symbol, order_type, lot_size, entry_time, entry_price, entry_reason, slippage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticket, symbol, order_type, lot_size, entry_time, entry_price, entry_reason, slippage))
        
        # ลงตาราง 2
        cursor.execute('''
            INSERT INTO market_context (ticket, rsi_entry, market_regime, volatility_threshold, risk_level)
            VALUES (?, ?, ?, ?, ?)
        ''', (ticket, rsi_entry, market_regime, vol_thresh, risk_level))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.getLogger("System").error(f"❌ DB Entry Error: {e}")

def log_trade_exit(ticket, exit_price, net_profit, max_float_p, max_float_l, exit_reason, balance_after):
    """ บันทึกตอนปิดไม้ (อัปเดตตารางเดิม) """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        exit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            UPDATE trade_history
            SET exit_time = ?, exit_price = ?, net_profit = ?, 
                max_floating_profit = ?, max_floating_loss = ?, exit_reason = ?, balance_after_trade = ?
            WHERE ticket = ?
        ''', (exit_time, exit_price, net_profit, max_float_p, max_float_l, exit_reason, balance_after, ticket))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logging.getLogger("System").error(f"❌ DB Exit Error: {e}")

def log_news_event(date_str, time_str, currency, impact, title):
    """ บันทึกข่าวลง Database (ป้องกันข่าวซ้ำด้วย) """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # เช็คก่อนว่าข่าวนี้เคยบันทึกของวันนี้ไปหรือยัง
        cursor.execute('SELECT 1 FROM news_history WHERE date=? AND time=? AND title=?', (date_str, time_str, title))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO news_history (date, time, currency, impact, title)
                VALUES (?, ?, ?, ?, ?)
            ''', (date_str, time_str, currency, impact, title))
            conn.commit()
        conn.close()
    except Exception as e:
        logging.getLogger("System").error(f"❌ DB News Error: {e}")