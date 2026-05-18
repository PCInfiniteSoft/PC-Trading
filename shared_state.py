import queue
import asyncio

BOT_STATE = "STOPPED"
log_queue = queue.Queue()
TRADE_LAYERS = {}
SCAN_COUNT = 0
AI_RETRY_COUNT = 0
CURRENT_LOOP_MINS = 5
FAST_GEAR_COUNT = 0
COOLDOWN_REMAINING = 0
CURRENT_RISK_LEVEL = 3
INITIAL_EQUITY = 0.0
MAX_DRAWDOWN_PERCENT = 10.0
IS_DAY_OFF = False
TODAY_NEWS_TIMES = []
NEWS_WINDOWS = []
ACTIVE_TRADE_TRACKER = {}
MT5_DISCONNECT_COUNT = 0
AI_DISCONNECT_COUNT = 0
DISCORD_DISCONNECT_COUNT = 0
LAST_REPORT_HOUR = -1

# 🔒 Asyncio locks
trade_layers_lock = asyncio.Lock()
tracker_lock = asyncio.Lock()

# 🔒 Regime stability — ป้องกัน regime เปลี่ยนบ่อยเกิน
# dict: { symbol: {"current": str, "count": int, "pending": str, "pending_count": int} }
REGIME_STABILITY = {}
REGIME_MIN_CONFIRMATIONS = 3   # ต้องเห็น regime เดิม 3 รอบติดกันก่อนเปลี่ยน

# Candle fingerprint — { symbol: last_candle_timestamp_int }
LAST_CANDLE_TIME = {}