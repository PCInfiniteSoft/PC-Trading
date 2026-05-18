"""
Monthly Trade Pattern Analysis (Candle-based)
รัน: วันอาทิตย์แรกของเดือน 00:00 UTC อัตโนมัติจาก trade_manager.py
ต้องการ: MT5 online (retry ทุก 15 นาที ถ้า offline)
เขียน: analysis/monthly/YYYY-MM.md
"""
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

ANALYSIS_DIR     = Path("analysis/monthly")
DB_PATH_DEFAULT  = "trading_history.db"
MAX_TRADES_BATCH = 30   # จำกัดเพื่อควบคุม token
CANDLES_BEFORE   = 5    # จำนวนแท่งเทียน M15 ก่อน entry

log = logging.getLogger("System")


def _get_candle_text(symbol: str, entry_time_str: str) -> str:
    """Fetch CANDLES_BEFORE M15 candles before entry_time from MT5."""
    try:
        import MetaTrader5 as mt5
        entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        rates = mt5.copy_rates_range(
            symbol, mt5.TIMEFRAME_M15,
            entry_dt - timedelta(hours=2), entry_dt
        )
        if rates is None or len(rates) == 0:
            return "  (no candle data)"
        candles = list(rates)[-CANDLES_BEFORE:]
        parts = []
        for i, c in enumerate(reversed(candles)):
            body = c["close"] - c["open"]
            tag  = "bullish" if body > 0 else "bearish" if body < 0 else "doji"
            parts.append(
                f"  -{i+1}: O={c['open']:.2f} H={c['high']:.2f} "
                f"L={c['low']:.2f} C={c['close']:.2f} ({tag})"
            )
        return "\n".join(parts)
    except Exception as e:
        return f"  (error: {e})"


async def run_monthly_analysis(db_path: str = DB_PATH_DEFAULT) -> str:
    """
    Fetch trades → get MT5 candles → ask GPT for pattern summary → write MD.
    Returns path of written file, or '' on failure.
    """
    import ai_engine as ai
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.utcnow()
    fname   = ANALYSIS_DIR / f"{now_utc.year}-{now_utc.month:02d}.md"

    # BTC: last 1 month, XAU: last 3 months (sample compensation)
    windows = {"BTCUSDm": 30, "XAUUSDm": 90}

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        data: dict[str, list] = {}
        for sym, days in windows.items():
            since = (now_utc - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                SELECT th.symbol, th.order_type, th.entry_time,
                       ROUND(th.net_profit, 2) AS net_profit,
                       mc.rsi_entry, mc.score
                FROM trade_history th
                LEFT JOIN market_context mc ON th.ticket = mc.ticket
                WHERE th.symbol = ?
                  AND th.exit_time IS NOT NULL
                  AND th.exit_time >= ?
                  AND th.net_profit IS NOT NULL
                ORDER BY ABS(th.net_profit) DESC
                LIMIT ?
            """, (sym, since, MAX_TRADES_BATCH))
            data[sym] = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"❌ [Monthly] DB error: {e}")
        return ""

    lines = [
        f"# Monthly Pattern Analysis — {now_utc.year}-{now_utc.month:02d}",
        f"*Generated: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC*",
        "",
    ]

    for sym, trades in data.items():
        window_label = "1 month" if sym == "BTCUSDm" else "3 months"
        if not trades:
            lines += [f"## {sym} ({window_label})", "ไม่มีข้อมูล trade เพียงพอ", ""]
            continue

        log.info(f"📊 [Monthly] วิเคราะห์ {sym} ({len(trades)} trades, {window_label})...")

        # Build trade text blocks for GPT
        trade_blocks = []
        for r in trades:
            result_label = "WIN" if r["net_profit"] > 0 else "LOSS"
            candle_text  = _get_candle_text(sym, r["entry_time"])
            rsi_val      = round(float(r["rsi_entry"]), 1) if r.get("rsi_entry") else "N/A"
            sc_val       = r.get("score", "N/A")
            trade_blocks.append(
                f"Trade: {sym} {r['order_type']} | result: {result_label} (${r['net_profit']:+.2f})\n"
                f"Candles (M15, newest first):\n{candle_text}\n"
                f"RSI: {rsi_val} | Score: {sc_val}"
            )

        batch_text = "\n\n---\n\n".join(trade_blocks)
        prompt = (
            f"You are analyzing {len(trades)} {sym} trades from the past {window_label}.\n"
            f"Summarize in Thai (max 200 words):\n"
            f"1. Common candle patterns in WIN trades (formation, RSI zone)\n"
            f"2. Common candle patterns in LOSS trades\n"
            f"3. 1-2 actionable recommendations to improve entry criteria\n\n"
            f"=== TRADE DATA ===\n{batch_text}"
        )

        try:
            resp = await ai.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3,
            )
            summary = resp.choices[0].message.content.strip()
        except Exception as e:
            summary = f"GPT error: {e}"
            log.error(f"❌ [Monthly] GPT error for {sym}: {e}")

        lines += [f"## {sym} Pattern Analysis ({window_label})", "", summary, ""]

    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"📊 [Monthly] เขียน analysis แล้ว: {fname}")
    return str(fname)


def load_context_for_director(max_chars: int = 800) -> str:
    """
    อ่าน monthly MD ล่าสุด ตัดให้ไม่เกิน max_chars
    สำหรับแนบใน DIRECTOR prompt เฉพาะต้นเดือน
    """
    files = sorted(ANALYSIS_DIR.glob("*.md"), reverse=True)[:1]
    if not files:
        return ""
    try:
        text = files[0].read_text(encoding="utf-8")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(truncated)"
        return text
    except Exception:
        return ""
