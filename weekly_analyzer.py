"""
Weekly Trade Analysis
รัน: ทุกวันอาทิตย์ 00:00 UTC อัตโนมัติจาก trade_manager.py
อ่าน: trade_history + market_context (7 วันที่แล้ว)
เขียน: analysis/weekly/YYYY-WNN.md
"""
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

ANALYSIS_DIR = Path("analysis/weekly")
DB_PATH_DEFAULT = "trading_history.db"

log = logging.getLogger("System")


def run_weekly_analysis(db_path: str = DB_PATH_DEFAULT) -> str:
    """
    Query last 7 days of trades, compute 5 metrics, write Markdown.
    Returns path of written file, or '' if no data.
    """
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.utcnow()
    iso = now_utc.isocalendar()
    fname = ANALYSIS_DIR / f"{iso[0]}-W{iso[1]:02d}.md"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        week_ago = (now_utc - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            SELECT th.symbol, th.order_type, th.entry_time,
                   ROUND(th.net_profit, 2) AS net_profit,
                   mc.rsi_entry, mc.market_regime, mc.allowed_dir,
                   mc.score
            FROM trade_history th
            LEFT JOIN market_context mc ON th.ticket = mc.ticket
            WHERE th.exit_time IS NOT NULL
              AND th.exit_time >= ?
              AND th.net_profit IS NOT NULL
            ORDER BY th.entry_time ASC
        """, (week_ago,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"❌ [Weekly] DB error: {e}")
        return ""

    if not rows:
        log.warning("📊 [Weekly] ไม่มีข้อมูล trade ในสัปดาห์นี้ — ข้าม")
        return ""

    lines = [
        f"# Weekly Analysis — {iso[0]}-W{iso[1]:02d}",
        f"*Generated: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC | Trades: {len(rows)}*",
        "",
    ]

    # ── 1. Performance by symbol ──────────────────────────────────
    lines.append("## Performance by Symbol")
    for sym in ["BTCUSDm", "XAUUSDm"]:
        t = [r for r in rows if r["symbol"] == sym]
        if not t:
            continue
        wins = sum(1 for r in t if r["net_profit"] > 0)
        pnl  = round(sum(r["net_profit"] for r in t), 2)
        wr   = wins * 100 // len(t)
        lines.append(f"- **{sym}**: {wins}/{len(t)} trades ({wr}%) | PnL: ${pnl:+.2f}")
    lines.append("")

    # ── 2. Hourly win rate ────────────────────────────────────────
    lines.append("## Hourly Win Rate (UTC)")
    lines.append("| Hour | Trades | Win% | PnL |")
    lines.append("|------|--------|------|-----|")
    hour_stats: dict = {}
    for r in rows:
        try:
            h = int(r["entry_time"][11:13])
        except Exception:
            continue
        d = hour_stats.setdefault(h, {"tp": 0, "sl": 0, "pnl": 0.0})
        if r["net_profit"] > 0:
            d["tp"] += 1
        else:
            d["sl"] += 1
        d["pnl"] += r["net_profit"]
    for h in sorted(hour_stats):
        d  = hour_stats[h]
        t  = d["tp"] + d["sl"]
        wr = d["tp"] * 100 // t if t else 0
        lines.append(f"| {h:02d} | {t} | {wr}% | ${d['pnl']:+.2f} |")
    lines.append("")

    # ── 3. Score effectiveness ────────────────────────────────────
    lines.append("## Score Effectiveness")
    lines.append("| Score | Trades | Win% |")
    lines.append("|-------|--------|------|")
    score_stats: dict = {}
    for r in rows:
        sc = r.get("score")
        if sc is None:
            continue
        try:
            sc = int(float(sc))
        except Exception:
            continue
        d = score_stats.setdefault(sc, {"tp": 0, "sl": 0})
        if r["net_profit"] > 0:
            d["tp"] += 1
        else:
            d["sl"] += 1
    for sc in sorted(score_stats):
        d  = score_stats[sc]
        t  = d["tp"] + d["sl"]
        wr = d["tp"] * 100 // t if t else 0
        lines.append(f"| {sc} | {t} | {wr}% |")
    lines.append("")

    # ── 4. DIRECTOR direction accuracy ───────────────────────────
    lines.append("## DIRECTOR Direction Accuracy")
    lines.append("| Direction | Trades | Win% | PnL |")
    lines.append("|-----------|--------|------|-----|")
    dir_stats: dict = {}
    for r in rows:
        d_key = r.get("allowed_dir") or "UNKNOWN"
        d = dir_stats.setdefault(d_key, {"tp": 0, "sl": 0, "pnl": 0.0})
        if r["net_profit"] > 0:
            d["tp"] += 1
        else:
            d["sl"] += 1
        d["pnl"] += r["net_profit"]
    for d_key, st in sorted(dir_stats.items()):
        t  = st["tp"] + st["sl"]
        wr = st["tp"] * 100 // t if t else 0
        lines.append(f"| {d_key} | {t} | {wr}% | ${st['pnl']:+.2f} |")
    lines.append("")

    # ── 5. Health summary ─────────────────────────────────────────
    all_wins   = [r["net_profit"] for r in rows if r["net_profit"] > 0]
    all_losses = [r["net_profit"] for r in rows if r["net_profit"] <= 0]
    avg_win    = round(sum(all_wins)   / len(all_wins),   2) if all_wins   else 0.0
    avg_loss   = round(sum(all_losses) / len(all_losses), 2) if all_losses else 0.0
    total_pnl  = round(sum(r["net_profit"] for r in rows), 2)

    # max drawdown (running peak-to-trough)
    running = peak = max_dd = 0.0
    for r in rows:
        running += r["net_profit"]
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    lines += [
        "## Health Summary",
        f"- Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:+.2f}",
        f"- Max Drawdown: ${max_dd:.2f}",
        f"- Total: {len(rows)} trades | PnL: ${total_pnl:+.2f}",
        "",
    ]

    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"📊 [Weekly] เขียน analysis แล้ว: {fname}")
    return str(fname)


def load_context_for_director(max_chars: int = 1200) -> str:
    """
    อ่าน weekly MD ล่าสุด 2 สัปดาห์ แล้วตัดให้ไม่เกิน max_chars
    สำหรับแนบใน DIRECTOR prompt
    """
    files = sorted(ANALYSIS_DIR.glob("*.md"), reverse=True)[:2]
    if not files:
        return ""
    parts = []
    for f in files:
        try:
            parts.append(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    combined = "\n\n---\n\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...(truncated)"
    return combined
