import requests
from bot_config import WEBHOOK_URL
import logging


def send_trade_notification(symbol, side, price, rsi, ticket):
    if not WEBHOOK_URL:
        logging.getLogger("System").warning("⚠️ ไม่ได้ตั้งค่า Webhook URL แจ้งเตือน Discord จะไม่ทำงาน")
        return

    color = 3066993 if side == "BUY" else 15158332
    data = {
        "embeds": [{
            "title": f"🚨 PCTrading Alert: {side} {symbol}",
            "color": color,
            "fields": [
                {"name": "Price", "value": f"{price:,.2f}", "inline": True},
                {"name": "RSI", "value": f"{rsi:.2f}", "inline": True},
                {"name": "Ticket", "value": str(ticket), "inline": False}
            ]
        }]
    }
    try:
        res = requests.post(WEBHOOK_URL, json=data, timeout=10)
        if res.status_code == 204:
            logging.getLogger("System").info("💬 แจ้งเตือนเข้า Discord แล้ว")
    except Exception as e:
        logging.getLogger("System").error(f"❌ Webhook Error: {e}")