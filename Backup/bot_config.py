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

SYMBOLS_CONFIG = {
    "BTCUSDm": {"lot": LOT, "sl_pts": 30000, "tp_pts": 50000, "threshold": 500.0},
    "XAUUSDm": {"lot": LOT, "sl_pts": 500, "tp_pts": 1000, "threshold": 2.5}
}