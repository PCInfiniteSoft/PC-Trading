# tools/replay_guardian_r.py  — read-only replay over the live trade_history
import sys, sqlite3
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from risk_manager import RiskManager

DB = sys.argv[1] if len(sys.argv) > 1 else "trading_history.db"
rm = RiskManager(db_path=DB)
conn = sqlite3.connect(DB); cur = conn.cursor()
# Every closed XAU trade, oldest-first; at each one, ask "would R have blocked the NEXT entry?"
rows = cur.execute("SELECT exit_time, exit_reason FROM trade_history "
                   "WHERE symbol='XAUUSDm' AND exit_time IS NOT NULL "
                   "ORDER BY exit_time ASC").fetchall()
for i in range(len(rows)):
    window = list(reversed(rows[:i+1]))[:3]          # newest-first top-3 as of this point
    now = datetime.strptime(rows[i][0], "%Y-%m-%d %H:%M:%S")
    blocked = rm.is_loss_streak_active("XAUUSDm", streak_n=3, cooldown_minutes=60,
                                       now=now, rows=window)
    flag = "  <== BLOCK next entry" if blocked else ""
    print(rows[i][0], rows[i][1][:24], flag)
conn.close()
