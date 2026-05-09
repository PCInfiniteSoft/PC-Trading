import MetaTrader5 as mt5
import os

def load_settings(file_path="settings.txt"):
    settings = {}
    if not os.path.exists(file_path): return settings
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                key, value = line.strip().split("=", 1)
                settings[key.strip()] = value.strip()
    return settings

conf = load_settings()
MAGIC_NUMBER = 999999
TOKEN = conf.get("TOKEN", "")
AI_KEY = conf.get("AI_KEY", "")
ACCOUNT_ID = int(conf.get("account_id", 0))
PWD = conf.get("pwd", "")
SRV = conf.get("srv", "")
LOT = float(conf.get("LOT", 0.01))

# 🟢 ล็อค Timeframe ไว้ที่ 5 นาทีเสมอ
TIMEFRAME = mt5.TIMEFRAME_M5

LOG_PATH = conf.get("LOG_PATH", "Logs")
REPORT_CHANNEL_ID = int(conf.get("REPORT_CHANNEL_ID", 0))
WEBHOOK_URL = conf.get("WEBHOOK_URL", "")

# ══════════════════════════════════════════════════════════════════
#  Per-symbol configuration
#  แยก parameter ออกเป็นรายคู่เงิน เพราะ BTC และ XAU มี behavior ต่างกันมาก
#
#  BTC characteristics:
#    - ATR% สูงกว่า XAU ~3x (0.06–0.19% vs 0.04–0.08%)
#    - RSI swing กว้างกว่า volatile มากกว่า
#    - ต้องการ threshold ที่ผ่อนปรนกว่า และ RSI buffer กว้างกว่า
#
#  XAU characteristics:
#    - ATR% แคบกว่า สม่ำเสมอกว่า
#    - RSI cycle ชัดเจน predictable กว่า
# ══════════════════════════════════════════════════════════════════

SYMBOLS_CONFIG = {
    "BTCUSDm": {
        "lot": LOT,

        # SL/TP เป็น points (1 point = $0.01 สำหรับ BTC)
        # BTC ราคา ~95,000 → sl 300 pts = $300 = ~0.3% ของราคา
        "sl_pts": 30000,
        "tp_pts": 50000,

        # Volatility threshold: % change ต่อ candle ที่ถือว่า spike
        # BTC ปกติวิ่ง 0.06–0.19% ต่อ candle → ตั้ง 0.25% พอดี
        "threshold": 0.25,

        # RSI entry buffer เพิ่มเติมสำหรับ BTC (จาก AI threshold)
        # เพราะ BTC RSI swing กว้างกว่า XAU มาก
        "rsi_buy_buffer": 5.0,   # ขยาย buy threshold ออก +5 จาก AI กำหนด
        "rsi_sell_buffer": 5.0,  # ขยาย sell threshold ออก +5 จาก AI กำหนด

        # ATR% ขั้นต่ำที่ถือว่าตลาดมี momentum พอ (BTC ต้องการสูงกว่า XAU)
        "min_atr_pct": 0.08,

        # Score threshold สำหรับ ANALYST (BTC ผ่อนปรนกว่า XAU เล็กน้อย)
        # เพราะ BTC มี supply/demand zone ชัดเจนน้อยกว่า
        "analyst_score_offset": -1,  # ลด trigger score ลง 1 จากค่า default

        # Max spread ที่ยอมรับได้ (BTC spread กว้างกว่า XAU)
        "max_spread_override": 8000,

        # Pullback detection — BTC pullback ลึกกว่า XAU
        "pullback_rsi_threshold": 42.0,  # RSI ต่ำกว่านี้ใน TRENDING_UP = Pullback entry
    },

    "XAUUSDm": {
        "lot": LOT,

        # XAU ราคา ~3300 → sl 500 pts = $5 = ~0.15% ของราคา
        "sl_pts": 500,
        "tp_pts": 1000,

        # XAU ปกติวิ่ง 0.04–0.08% ต่อ candle → ตั้ง 0.15%
        "threshold": 0.15,

        # XAU RSI swing แคบกว่า ไม่ต้องการ buffer มาก
        "rsi_buy_buffer": 0.0,
        "rsi_sell_buffer": 0.0,

        # ATR% ขั้นต่ำสำหรับ XAU
        "min_atr_pct": 0.04,

        # XAU ใช้ default score ปกติ
        "analyst_score_offset": 0,

        # XAU spread แคบกว่า
        "max_spread_override": 5000,

        # XAU pullback ไม่ลึกเท่า BTC
        "pullback_rsi_threshold": 45.0,
    }
}
