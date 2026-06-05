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

# [Gate J] หยุด trade ทั้งวันถ้า realized loss วันนี้ >= limit นี้
# ปรับได้ใน settings.txt: DAILY_LOSS_LIMIT=100
DAILY_LOSS_LIMIT = float(conf.get("DAILY_LOSS_LIMIT", 100.0))
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
        "lot_max": 0.05,

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

        # GUARDIAN-M dynamic slippage ceiling: dyn = base + a*ATR_pts + b*spread_pts, capped.
        # base = previous static max_slip (non-regression). ATR coeff is small because
        # ATR-in-points is large at point=0.01 (~3e4). spread ~1x current spread (~1008pts).
        "slip_base": 600,
        "slip_a_atr": 0.02,
        "slip_b_spread": 1.0,
        "slip_cap": 1800,

        # Pullback detection — BTC pullback ลึกกว่า XAU
        "pullback_rsi_threshold": 42.0,  # RSI ต่ำกว่านี้ใน TRENDING_UP = Pullback entry

        # ── Strategy profile (SP-A) — per-symbol logic, read by trade_manager ──
        # NOTE: only btc_momentum_guards + entry_paths(trend_sell) + xau_trend_sell_enabled
        # are consumed by live code today. xau_buy_only / score_blacklist / "mean_reversion"
        # are declarative (XAU buy-only + score-blacklist still enforced by RiskManager
        # guards G/H, not these flags). Full wiring lands in SP-B — do not assume flipping
        # them changes behavior yet.
        "strategy": {
            "entry_paths": ["mean_reversion"],
            "guards": {
                "xau_buy_only": False,
                "score_blacklist": {8},
                "btc_momentum_guards": True,
            },
            # st3 trend-sell config (used only when xau_trend_sell_enabled is True)
            "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
            "xau_trend_sell_enabled": False,
        },
    },

    "XAUUSDm": {
        "lot": LOT,
        "lot_max": 0.05,

        # XAU ราคา ~3300 → sl 500 pts = $5 = ~0.15% ของราคา
        "sl_pts": 500,
        "tp_pts": 1000,

        # XAU ปกติวิ่ง 0.04–0.08% ต่อ candle → ตั้ง 0.15%
        "threshold": 0.15,

        # XAU RSI swing แคบกว่า ไม่ต้องการ buffer มาก
        "rsi_buy_buffer": 3.0,
        "rsi_sell_buffer": 0.0,

        # ATR% ขั้นต่ำสำหรับ XAU
        "min_atr_pct": 0.04,

        # [FIX] ลด trigger score ลง 1 เท่า BTC — XAU H1+RSI pullback score อาจไม่ถึง 6 ใน trending market
        "analyst_score_offset": -1,

        # XAU spread แคบกว่า
        "max_spread_override": 5000,

        # GUARDIAN-M dynamic slippage ceiling (see BTC block). point=0.001, spread ~280pts.
        "slip_base": 300,
        "slip_a_atr": 0.02,
        "slip_b_spread": 1.0,
        "slip_cap": 900,

        # XAU pullback ไม่ลึกเท่า BTC
        "pullback_rsi_threshold": 45.0,

        # ── Strategy profile (SP-A) — per-symbol logic, read by trade_manager ──
        # NOTE: only btc_momentum_guards + entry_paths(trend_sell) + xau_trend_sell_enabled
        # are consumed by live code today. xau_buy_only / score_blacklist / "mean_reversion"
        # are declarative (XAU buy-only + score-blacklist still enforced by RiskManager
        # guards G/H, not these flags). Full wiring lands in SP-B — do not assume flipping
        # them changes behavior yet.
        "strategy": {
            "entry_paths": ["mean_reversion", "trend_sell"],
            "guards": {
                "xau_buy_only": True,
                "score_blacklist": {8},
            },
            # st3 trend-sell config (used only when xau_trend_sell_enabled is True)
            "trend_sell": {"trigger": "rsi", "rsi_level": 50.0, "lot_mult": 0.5},
            "xau_trend_sell_enabled": False,
        },
    }
}
