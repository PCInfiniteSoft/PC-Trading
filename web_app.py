"""
PC Trading — Web Dashboard Server
รัน: python web_app.py
เปิดเบราว์เซอร์: http://localhost:8080
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, abort, request

app = Flask(__name__, static_folder=".")

STATUS_FILE = "bot_status.json"
DB_FILE     = "trading_history.db"

# ── In-memory log buffer ───────────────────────────────────────
_log_buffer = []
_log_lock   = threading.Lock()
MAX_LOGS    = 200

def push_log(time_str: str, level: str, msg: str):
    """เรียกจาก GUIHandler เพื่อ push log เข้า buffer"""
    with _log_lock:
        _log_buffer.append({"time": time_str, "level": level, "msg": msg})
        if len(_log_buffer) > MAX_LOGS:
            _log_buffer.pop(0)

# ── Helpers ───────────────────────────────────────────────────

def _read_status() -> dict:
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _query_db(sql: str, params: tuple = ()) -> list:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# REST API
# ══════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    data = _read_status()
    with _log_lock:
        data["logs"] = list(reversed(_log_buffer[-50:]))
    return jsonify(data)


@app.route("/api/trades")
def api_trades():
    rows = _query_db("""
        SELECT th.ticket, th.symbol, th.order_type, th.lot_size,
               th.entry_time, th.entry_price, th.exit_time, th.exit_price,
               ROUND(th.net_profit,2) AS net_profit, th.exit_reason,
               mc.market_regime, mc.rsi_entry, mc.macro_bias, mc.allowed_dir, mc.risk_level
        FROM trade_history th
        LEFT JOIN market_context mc ON th.ticket = mc.ticket
        WHERE th.exit_time IS NOT NULL
        ORDER BY th.exit_time DESC LIMIT 200
    """)
    return jsonify({"trades": rows, "count": len(rows)})


@app.route("/api/performance")
def api_performance():
    daily = _query_db("""
        SELECT date(exit_time) AS day,
               ROUND(SUM(net_profit),2) AS pnl,
               COUNT(*) AS trades,
               SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) AS wins
        FROM trade_history WHERE exit_time IS NOT NULL
        GROUP BY date(exit_time) ORDER BY day ASC LIMIT 90
    """)
    by_strategy = _query_db("""
        SELECT mc.market_regime AS strategy,
               COUNT(*) AS trades,
               SUM(CASE WHEN th.net_profit>0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(th.net_profit),2) AS total_pnl,
               ROUND(AVG(th.net_profit),4) AS avg_pnl
        FROM trade_history th
        LEFT JOIN market_context mc ON th.ticket=mc.ticket
        WHERE th.exit_time IS NOT NULL
          AND th.exit_time >= datetime('now','-30 days')
        GROUP BY mc.market_regime ORDER BY total_pnl DESC
    """)
    for r in by_strategy:
        r["win_rate"] = round(r["wins"]/r["trades"]*100, 1) if r["trades"] else 0

    by_symbol = _query_db("""
        SELECT symbol, COUNT(*) AS trades,
               SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(net_profit),2) AS total_pnl
        FROM trade_history
        WHERE exit_time IS NOT NULL AND exit_time >= datetime('now','-30 days')
        GROUP BY symbol ORDER BY total_pnl DESC
    """)
    for r in by_symbol:
        r["win_rate"] = round(r["wins"]/r["trades"]*100, 1) if r["trades"] else 0

    return jsonify({"daily": daily, "by_strategy": by_strategy, "by_symbol": by_symbol})


@app.route("/api/logs")
def api_logs():
    with _log_lock:
        return jsonify({"logs": list(reversed(_log_buffer))})


@app.route("/api/control", methods=["POST"])
def api_control():
    try:
        import shared_state, logging
        data   = request.get_json(force=True) or {}
        action = data.get("action", "").upper()
        log    = logging.getLogger("System")

        if action == "START":
            if shared_state.BOT_STATE not in ["RUNNING", "WAITING"]:
                shared_state.BOT_STATE = "WAITING"
                log.warning("▶️ [Web] สั่ง Start Bot ผ่าน Dashboard")
        elif action == "STOP":
            shared_state.BOT_STATE = "STOPPED"
            log.error("🛑 [Web] สั่ง Stop Bot ผ่าน Dashboard")
        elif action == "PAUSE":
            shared_state.BOT_STATE = "COOLDOWN"
            shared_state.COOLDOWN_REMAINING = 30
            log.warning("⏸️ [Web] สั่ง Pause Bot ผ่าน Dashboard (Cooldown 30m)")
        elif action == "RESTART":
            shared_state.BOT_STATE = "STOPPED"
            log.warning("🔄 [Web] สั่ง Restart Bot ผ่าน Dashboard")
            import threading, time
            def _restart():
                time.sleep(2)
                shared_state.BOT_STATE = "WAITING"
            threading.Thread(target=_restart, daemon=True).start()
        elif action == "RISK":
            level = int(data.get("level", shared_state.CURRENT_RISK_LEVEL))
            level = max(1, min(5, level))
            shared_state.CURRENT_RISK_LEVEL = level
            log.warning(f"⚠️ [Web] ปรับ Risk Level เป็น {level} ผ่าน Dashboard")
        else:
            return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400

        return jsonify({"ok": True, "action": action, "state": shared_state.BOT_STATE})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/health")
def api_health():
    try:
        mtime = os.path.getmtime(STATUS_FILE)
        age   = round(time.time() - mtime, 1)
        last  = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
    except Exception:
        age, last = 999, "N/A"
    return jsonify({"ok": age < 60, "last_update": last, "age_secs": age,
                    "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ══════════════════════════════════════════════════════════════
# Serve dashboard.html
# ══════════════════════════════════════════════════════════════

DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"

@app.route("/")
def index():
    if DASHBOARD_HTML.exists():
        return send_from_directory(str(DASHBOARD_HTML.parent), "dashboard.html")
    return ("<h2 style='font-family:monospace;color:#0f0;background:#000;padding:20px'>"
            "⚠️ dashboard.html not found — place it next to web_app.py</h2>"), 404


@app.route("/<path:filename>")
def static_files(filename):
    p = Path(__file__).parent / filename
    if p.exists() and p.is_file():
        return send_from_directory(str(p.parent), p.name)
    abort(404)


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

def start_web_server(host: str = "0.0.0.0", port: int = 8080):
    """เรียกจาก gui_main.py เพื่อรัน Flask ใน background thread"""
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True, name="WebDashboard"
    )
    t.start()
    import logging
    logging.getLogger("System").info(f"🌐 Web Dashboard เริ่มทำงานที่ http://localhost:{port}")


if __name__ == "__main__":
    print("🌐 PC Trading Web Dashboard  →  http://localhost:8080\n")
    app.run(host="0.0.0.0", port=8080, debug=True)
