import json
import shared_state
import MetaTrader5 as mt5
import trade_manager as tm
import ai_engine as ai
from bot_config import SYMBOLS_CONFIG

def save_web_status():
    status_data = {
        "state": shared_state.BOT_STATE,
        "balance": mt5.account_info().balance if mt5.account_info() else 0,
        "risk_level": shared_state.CURRENT_RISK_LEVEL,
        "symbols": {}
    }
    for s in SYMBOLS_CONFIG:
        # 🟢 ดึงข้อมูล Macro ของ Agent 0 มารวมด้วย
        macro_data = getattr(shared_state, 'MACRO_DATA', {}).get(s, {})
        
        status_data["symbols"][s] = {
            "price": mt5.symbol_info_tick(s).ask if mt5.symbol_info_tick(s) else 0,
            "regime": ai.STRATEGY_DATA[s]["regime"],
            "rsi": tm.get_rsi(s),
            "macro_bias": macro_data.get('bias', 'N/A'),
            "allowed_dir": macro_data.get('allowed_direction', 'BOTH')
        }

    with open("bot_status.json", "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=4)