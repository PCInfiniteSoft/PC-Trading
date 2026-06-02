import sqlite3
conn = sqlite3.connect('trading_history.db')
c = conn.cursor()

print("=== ALL-TIME by symbol+direction ===")
c.execute("""
SELECT symbol, order_type,
  COUNT(*) total,
  SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) wins,
  ROUND(SUM(net_profit),2) pnl,
  ROUND(AVG(net_profit),2) avg_pnl,
  ROUND(AVG(CASE WHEN net_profit>0 THEN net_profit END),2) avg_win,
  ROUND(AVG(CASE WHEN net_profit<0 THEN net_profit END),2) avg_loss
FROM trade_history
GROUP BY symbol, order_type
""")
for r in c.fetchall(): print(r)

print("\n=== By HOUR UTC (all time) ===")
c.execute("""
SELECT symbol, CAST(strftime('%H', entry_time) AS INT) h,
  COUNT(*) t,
  SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) w,
  ROUND(SUM(net_profit),2) pnl
FROM trade_history
GROUP BY symbol, h ORDER BY symbol, pnl
""")
for r in c.fetchall(): print(r)

print("\n=== RECENT 20 trades ===")
c.execute("""
SELECT entry_time, symbol, order_type, ROUND(net_profit,2), exit_reason, entry_reason
FROM trade_history ORDER BY entry_time DESC LIMIT 20
""")
for r in c.fetchall(): print(r)

print("\n=== SL vs TP count ===")
c.execute("""
SELECT symbol, order_type,
  SUM(CASE WHEN exit_reason LIKE '%Stop Loss%' THEN 1 ELSE 0 END) sl_count,
  SUM(CASE WHEN exit_reason LIKE '%Take Profit%' THEN 1 ELSE 0 END) tp_count,
  SUM(CASE WHEN exit_reason LIKE '%RSI Hit%' THEN 1 ELSE 0 END) rsi_tp_count,
  COUNT(*) total
FROM trade_history
GROUP BY symbol, order_type
""")
for r in c.fetchall(): print(r)

conn.close()
