import json
import sqlite3
import time
import shared_state
import MetaTrader5 as mt5
import trade_manager as tm
import ai_engine as ai
import discord_manager
from bot_config import SYMBOLS_CONFIG
from datetime import datetime

# ── ประวัติ Equity สำหรับกราฟ (เก็บ 60 จุดล่าสุด) ──
_equity_history = []

def _get_open_positions():
    """แปลง MT5 positions เป็น list ที่ JSON-serializable"""
    positions = mt5.positions_get() or []
    result = []
    for p in positions:
        tracker = shared_state.ACTIVE_TRADE_TRACKER.get(p.ticket, {"max_p": 0.0, "max_l": 0.0})
        strat = ai.STRATEGY_DATA.get(p.symbol, {})
        macro = getattr(shared_state, 'MACRO_DATA', {}).get(p.symbol, {})
        result.append({
            "ticket":       p.ticket,
            "symbol":       p.symbol,
            "side":         "LONG" if p.type == mt5.ORDER_TYPE_BUY else "SHORT",
            "strategy":     p.comment.split(":")[0] if p.comment else "N/A",
            "entry_price":  round(p.price_open, 5),
            "tp":           round(p.tp, 5),
            "sl":           round(p.sl, 5),
            "volume":       p.volume,
            "profit":       round(p.profit + p.swap, 2),
            "profit_pct":   round((p.profit + p.swap) / (p.price_open * p.volume * 100 + 0.001) * 100, 2),
            "max_profit":   round(tracker["max_p"], 2),
            "max_loss":     round(tracker["max_l"], 2),
            "regime":       strat.get("regime", "N/A"),
            "macro_bias":   macro.get("bias", "N/A"),
            "allowed_dir":  macro.get("allowed_direction", "BOTH"),
            "open_time":    p.time,
        })
    return result

def _get_recent_trades(limit=20):
    """ดึงประวัติการเทรดล่าสุดจาก DB"""
    try:
        conn = sqlite3.connect("trading_history.db")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT th.ticket, th.symbol, th.order_type, th.lot_size,
                   th.entry_time, th.entry_price, th.exit_time, th.exit_price,
                   th.net_profit, th.exit_reason,
                   mc.market_regime, mc.rsi_entry, mc.macro_bias, mc.allowed_dir
            FROM trade_history th
            LEFT JOIN market_context mc ON th.ticket = mc.ticket
            WHERE th.exit_time IS NOT NULL
            ORDER BY th.exit_time DESC
            LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []

def _get_strategy_stats(days=7):
    """สรุปผลกลยุทธ์รายตัว 7 วัน"""
    try:
        conn = sqlite3.connect("trading_history.db")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT mc.market_regime AS strategy,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN th.net_profit > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(th.net_profit) AS total_pnl
            FROM trade_history th
            LEFT JOIN market_context mc ON th.ticket = mc.ticket
            WHERE th.exit_time IS NOT NULL
              AND th.exit_time >= datetime('now', ?)
            GROUP BY mc.market_regime
            ORDER BY total_pnl DESC
        """, (f'-{days} days',))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] > 0 else 0
            d["total_pnl"] = round(d["total_pnl"] or 0, 2)
            rows.append(d)
        conn.close()
        return rows
    except Exception:
        return []

def _get_daily_performance(days=30):
    """ดึง daily P&L สำหรับ equity curve chart"""
    try:
        conn = sqlite3.connect("trading_history.db")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT date(exit_time) AS day,
                   SUM(net_profit) AS daily_pnl,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS wins
            FROM trade_history
            WHERE exit_time IS NOT NULL
              AND exit_time >= datetime('now', ?)
            GROUP BY date(exit_time)
            ORDER BY day ASC
        """, (f'-{days} days',))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []

def _get_news_calendar():
    """แปลง NEWS_WINDOWS เป็น list ที่ JSON-serializable"""
    windows = getattr(shared_state, 'NEWS_WINDOWS', [])
    result = []
    for w in windows:
        result.append({
            "title": w.get("title", ""),
            "time":  w["time"].strftime("%H:%M") if w.get("time") else "",
            "start": w["start"].strftime("%H:%M") if w.get("start") else "",
            "end":   w["end"].strftime("%H:%M") if w.get("end") else "",
        })
    return result

def _drain_log_queue(max_entries=50):
    """ดึง logs จาก shared_state.log_queue (non-blocking)"""
    logs = []
    try:
        while not shared_state.log_queue.empty() and len(logs) < max_entries:
            msg, level = shared_state.log_queue.get_nowait()
            logs.append({"time": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg})
    except Exception:
        pass
    return logs

def save_web_status():
    global _equity_history

    acc = mt5.account_info()
    balance  = round(acc.balance,  2) if acc else 0.0
    equity   = round(acc.equity,   2) if acc else 0.0
    margin   = round(acc.margin,   2) if acc else 0.0
    free_mrg = round(acc.margin_free, 2) if acc else 0.0

    # อัปเดต equity history (max 60 จุด)
    _equity_history.append({"t": datetime.now().strftime("%H:%M"), "v": equity})
    if len(_equity_history) > 60:
        _equity_history.pop(0)

    # Open positions
    open_positions = _get_open_positions()
    float_pl = round(sum(p["profit"] for p in open_positions), 2)

    # Drawdown
    initial_equity = getattr(shared_state, 'INITIAL_EQUITY', equity) or equity
    drawdown_pct = round((initial_equity - equity) / initial_equity * 100, 2) if initial_equity > 0 else 0.0

    # Symbol data
    symbols_data = {}
    for s in SYMBOLS_CONFIG:
        macro_data = getattr(shared_state, 'MACRO_DATA', {}).get(s, {})
        tick = mt5.symbol_info_tick(s)
        info = mt5.symbol_info(s)
        rsi  = tm.get_rsi(s)
        strat = ai.STRATEGY_DATA.get(s, {})
        is_open = bool(info and tick and info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL and (time.time() - tick.time < 3600))
        symbols_data[s] = {
            "price":          round(tick.ask, 5) if tick else 0,
            "bid":            round(tick.bid, 5) if tick else 0,
            "spread":         round((tick.ask - tick.bid) / info.point, 1) if tick and info else 0,
            "market_status":  "OPEN" if is_open else "CLOSED",
            "regime":         strat.get("regime", "N/A"),
            "rsi":            round(rsi, 2) if rsi else 0,
            "buy_targets":    strat.get("buy", []),
            "sell_targets":   strat.get("sell", []),
            "threshold":      strat.get("threshold", 0),
            "macro_bias":     macro_data.get("bias", "N/A"),
            "allowed_dir":    macro_data.get("allowed_direction", "BOTH"),
            "h4_trend":       macro_data.get("h4_trend", "N/A"),
            "d1_trend":       macro_data.get("d1_trend", "N/A"),
        }

    # Cooldown state label
    state_str = shared_state.BOT_STATE
    if state_str == "COOLDOWN":
        state_str = f"COOLDOWN ({getattr(shared_state, 'COOLDOWN_REMAINING', 0)}m)"

    status_data = {
        "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # ── Account ──
        "state":            state_str,
        "balance":          balance,
        "equity":           equity,
        "margin":           margin,
        "free_margin":      free_mrg,
        "float_pl":         float_pl,
        "drawdown_pct":     drawdown_pct,
        "initial_equity":   round(initial_equity, 2),
        # ── Bot stats ──
        "risk_level":       shared_state.CURRENT_RISK_LEVEL,
        "max_drawdown_pct": shared_state.MAX_DRAWDOWN_PERCENT,
        "scan_count":       shared_state.SCAN_COUNT,
        "loop_mins":        shared_state.CURRENT_LOOP_MINS,
        "is_day_off":       shared_state.IS_DAY_OFF,
        "mt5_disconnects":  getattr(shared_state, 'MT5_DISCONNECT_COUNT', 0),
        "ai_disconnects":   getattr(shared_state, 'AI_DISCONNECT_COUNT', 0),
        "ai_online":        ai.AI_IS_ONLINE,
        "ai_error_code":    ai.AI_ERROR_CODE,
        "fast_gear_count":  shared_state.FAST_GEAR_COUNT,
        "cooldown_rem":     getattr(shared_state, 'COOLDOWN_REMAINING', 0),
        # ── Trade data ──
        "open_positions":   open_positions,
        "open_count":       len(open_positions),
        "recent_trades":    _get_recent_trades(20),
        "strategy_stats":   _get_strategy_stats(7),
        "daily_performance":_get_daily_performance(30),
        # ── Charts ──
        "equity_history":   list(_equity_history),
        # ── Symbols ──
        "symbols":          symbols_data,
        # ── Logs ──
        "logs":             _drain_log_queue(50),
        # ── News Calendar ──
        "news_calendar":    _get_news_calendar(),
        # ── Connection status ──
        "discord_online":   discord_manager.bot.is_ready(),
        "mt5_online":       mt5.account_info() is not None,
    }

    with open("bot_status.json", "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False, default=str)