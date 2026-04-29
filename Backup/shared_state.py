import queue

# สถานะหลักของบอท: RUNNING, PAUSED, STOPPED, COOLDOWN
BOT_STATE = "STOPPED" 
log_queue = queue.Queue()

# เก็บข้อมูลการเทรดแต่ละไม้ (5 Layers) เพื่อป้องกันการเปิดซ้ำ
TRADE_LAYERS = {} 

# ตัวแปรควบคุม Gear Shift และ Circuit Breaker
SCAN_COUNT = 0
AI_RETRY_COUNT = 0
CURRENT_LOOP_MINS = 5
FAST_GEAR_COUNT = 0 # นับรอบที่เจอแรงกระชากติดกัน
COOLDOWN_REMAINING = 0 # เวลาจำศีลที่เหลือ (นาที)
CURRENT_RISK_LEVEL = 3
INITIAL_EQUITY = 0.0
MAX_DRAWDOWN_PERCENT = 10.0
IS_DAY_OFF = False