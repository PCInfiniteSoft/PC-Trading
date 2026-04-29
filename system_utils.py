import json
import MetaTrader5 as mt5
import ai_engine as ai
import trade_manager as tm
import shared_state
from bot_config import SYMBOLS_CONFIG

def save_web_status():
    status_data = {
        "state": shared_state.BOT_STATE,
        "balance": mt5.account_info().balance if mt5.account_info() else 0,
        "risk_level": shared_state.CURRENT_RISK_LEVEL,
        "symbols": {}
    }
    for s in SYMBOLS_CONFIG:
        status_data["symbols"][s] = {
            "price": mt5.symbol_info_tick(s).ask if mt5.symbol_info_tick(s) else 0,
            "regime": ai.STRATEGY_DATA[s]["regime"],
            "rsi": tm.get_rsi(s)
        }

    with open("bot_status.json", "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=4)