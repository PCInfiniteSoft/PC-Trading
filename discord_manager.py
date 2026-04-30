import discord
import asyncio
import os
import sys
import logging
import sqlite3
import shared_state
import threading
import ai_engine as ai
import MetaTrader5 as mt5
import advanced_indicators as adv
import trade_manager as tm
from bot_config import *
from discord.ext import commands, tasks
from datetime import datetime, time
from trade_manager import get_rsi, is_safe_trading_time

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

def run_discord_bot_thread():

    def run_async_loop():
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:

            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            logging.getLogger("System").error(f"❌ Discord Bot Thread Error: {e}")

    discord_thread = threading.Thread(target=run_async_loop, daemon=True)
    discord_thread.start()
    logging.getLogger("System").info("🚀 บอท Discord เริ่มทำงานใน Background Thread แล้ว")

def get_today_deals(symbol=None):
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day)
    h = mt5.history_deals_get(start_of_day, now)
    
    if not h: return []
    
    if symbol:
        return [d for d in h if d.symbol == symbol and d.entry == mt5.DEAL_ENTRY_OUT]
    return [d for d in h if d.entry == mt5.DEAL_ENTRY_OUT]

def get_win_loss_text():
    deals = get_today_deals()
    if not deals: return "No trades today"
    
    profit = sum(d.profit for d in deals)
    wins = len([d for d in deals if d.profit > 0])
    losses = len([d for d in deals if d.profit < 0])
    return f"W:{wins} L:{losses} | P/L: {profit:.2f} USD"

def build_symbol_status(s):
    strat = ai.STRATEGY_DATA.get(s, {})
    regime = strat.get("regime", "N/A")
    rsi = tm.get_rsi(s)
    rsi_str = f"{rsi:.2f}" if rsi else "N/A"
    
    buy_target = strat.get('buy', ['--'])[0] if strat.get('buy') else '--'
    sell_target = strat.get('sell', ['--'])[0] if strat.get('sell') else '--'
    
    return (f"🔷 **{s}**\n"
            f"🤖 Market : `{regime}`\n"
            f"🛡️ Risk Level : `{shared_state.CURRENT_RISK_LEVEL}/5`\n"
            f"🎯 Target : Buy < {buy_target} | Sell > {sell_target}\n"
            f"📈 Current RSI : `{rsi_str}`\n"
            f"-----------------------------------")

# ==========================================
# 📝 ฟังก์ชันส่ง Report รูปแบบต่างๆ
# ==========================================
def get_today_db_stats(symbol=None):
    """ดึงยอดเทรดที่ปิดแล้วของวันนี้ พร้อมแยก Win/Loss จาก Database"""
    try:
        from database_manager import DB_NAME
        import sqlite3
        from datetime import datetime
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        if symbol:
            cursor.execute("SELECT net_profit FROM trade_history WHERE symbol=? AND exit_time LIKE ?", (symbol, f"{today_str}%"))
        else:
            cursor.execute("SELECT net_profit FROM trade_history WHERE exit_time LIKE ?", (f"{today_str}%",))
            
        rows = cursor.fetchall()
        conn.close()
        
        # คำนวณตระกร้า Win / Loss
        count = len(rows)
        profit = sum(r[0] for r in rows if r[0] is not None)
        wins = sum(1 for r in rows if r[0] and r[0] > 0)
        losses = sum(1 for r in rows if r[0] and r[0] <= 0)
        
        return count, profit, wins, losses
    except Exception as e:
        return 0, 0.0, 0, 0

async def send_startup_report(channel=None):
    if not channel:
        from bot_config import REPORT_CHANNEL_ID
        channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"

    msg = f"📊 **[Started PCTrading 2.17]**\n"
    msg += f"⏰ **[Time: {now_str}]**\n"
    msg += "========================\n"
    
    for s in SYMBOLS_CONFIG:
        is_open = "🟢" if is_safe_trading_time(s) else "🔴"
        strat = ai.STRATEGY_DATA.get(s, {})
        regime = strat.get('regime', 'N/A')
        rsi_val = get_rsi(s)
        rsi_text = f"{rsi_val:.2f}" if rsi_val is not None else "N/A"
        
        msg += f"{is_open} **{s}** `[{regime}]`\n"
        msg += f"📈 Current RSI : `{rsi_text}`\n"
        
    msg += "========================\n"
    msg += f"💵 **Initial Balance:** `{balance} USD`\n"
    msg += "========================\n"
    
    state_str = getattr(shared_state, 'BOT_STATE', 'WAITING')
    mt5_status = "✅ Connected" if mt5.terminal_info() else "❌ Disconnected"
    ai_status = "✅ Online" if ai.AI_IS_ONLINE else "❌ Offline"
    
    msg += f"🔌 **System Connection** `[{state_str}]`\n"
    msg += f"MT5: {mt5_status}\n"
    msg += f"AI: {ai_status}\n"
    msg += "Discord: ✅ Online"
    
    await channel.send(msg)

async def send_closing_report(channel=None):
    if not channel:
        from bot_config import REPORT_CHANNEL_ID
        channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return
    # 🟢 แจ้งเตือนสั้นๆ ตามที่พี่โก้ต้องการ
    await channel.send("🛑 **ระบบถูกปิด**")

async def send_end_day_report(channel=None):
    if not channel:
        from bot_config import REPORT_CHANNEL_ID
        channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    import MetaTrader5 as mt5
    from datetime import datetime
    import shared_state
    from bot_config import SYMBOLS_CONFIG
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"

    msg = f"🛑 **[End-Day PCTrading 2.17]**\n"
    msg += f"⏰ **[Time: {now_str}]**\n"
    msg += "========================\n"
    
    total_closed_count, total_net_profit, total_wins, total_losses = get_today_db_stats()

    for s in SYMBOLS_CONFIG:
        sym_count, sym_profit, sym_wins, sym_losses = get_today_db_stats(s)
        if sym_count > 0: 
            msg += f"🔷 **{s}**\n"
            msg += f"📦 Today P/L : `{sym_profit:+.2f} USD`\n"
            msg += f"🛡️ Today's W/L : `W{sym_wins}|L{sym_losses}` `[{sym_count} Orders]`\n"

    msg += "========================\n"
    msg += f"💰 Total P/L : `{total_net_profit:+.2f} USD`\n"
    
    total_icon = "🟢" if total_net_profit >= 0 else "🔴"
    msg += f"{total_icon} Total W/L : `W{total_wins}|L{total_losses}`\n"
    msg += f"📦 Total Trades : `{total_closed_count} Orders`\n"
    msg += f"💵 End-Day Balance : `{balance} USD`\n"
    msg += "========================\n"
    
    mt5_drops = getattr(shared_state, 'MT5_DISCONNECT_COUNT', 0)
    ai_drops = getattr(shared_state, 'AI_DISCONNECT_COUNT', 0)
    dc_drops = getattr(shared_state, 'DISCORD_DISCONNECT_COUNT', 0)
    
    msg += f"⚠️ **System Health**\n"
    msg += f"`MT5: {mt5_drops} | AI: {ai_drops} | Discord: {dc_drops}`"
    
    await channel.send(msg)

async def send_status_report(title="[Status Report]"):
    channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    acc = mt5.account_info()
    curr_bal = acc.balance if acc else 0.0
    
    positions = mt5.positions_get()
    unrealized_pl = sum([(p.profit + p.swap) for p in positions]) if positions else 0.0
    total_closed_count, total_net_profit, total_wins, total_losses = get_today_db_stats()

    msg = f"📊 **{title}**\n========================\n"

    for symbol, strat in ai.STRATEGY_DATA.items():
        if not strat: continue

        tick = mt5.symbol_info_tick(symbol)
        is_open = "🟢" if tick and (datetime.now().timestamp() - tick.time < 300) else "🔴"
        
        rsi_text = "N/A"
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 15)
        if rates is not None and len(rates) > 0:
            prices = [r['close'] for r in rates]
            rsi_val = adv.calculate_rsi(prices)
            if rsi_val: rsi_text = f"{rsi_val[-1]:.2f}"
            
        sym_count, sym_profit, sym_wins, sym_losses = get_today_db_stats(symbol)
        
        msg += f"{is_open} **{symbol}** `[{strat.get('regime', 'N/A')}]`\n"
        msg += f"🛡️ Risk : `{shared_state.CURRENT_RISK_LEVEL}/5`\n"
        msg += f"📈 Current RSI : `{rsi_text}`\n"
        msg += f"📦 Trades : `{sym_profit:+.2f} USD` `[W:{sym_wins} | L:{sym_losses}]`\n"

    msg += "========================\n"
    
    unrealized_icon = "🟢" if unrealized_pl >= 0 else "🔴"
    net_icon = "🟢" if total_net_profit >= 0 else "🔴"
    
    msg += f"💰 **Today's P/L (Unrealized) :** `{unrealized_icon} {unrealized_pl:+.2f} USD`\n"
    msg += f"📦 Total Trades : `{total_closed_count} Orders` `[W:{total_wins} | L:{total_losses}]`\n"
    msg += f"**Net Profit :** `{net_icon} {total_net_profit:+.2f} USD`\n"
    msg += f"💵 **Current Balance:** `{curr_bal:.2f} USD`\n"
    msg += "========================\n"
    
    sys_state = shared_state.BOT_STATE
    if sys_state == "COOLDOWN":
        sys_state = f"Cooldown ({shared_state.COOLDOWN_REMAINING}m)"
        
    mt5_status = "✅ Connected" if mt5.terminal_info() else "❌ Disconnected"
    ai_status = "✅ Online" if ai.AI_IS_ONLINE else "❌ Offline"
    
    msg += f"🔌 **System Connection** `[{sys_state}]`\n"
    msg += f"MT5: {mt5_status} `({getattr(shared_state, 'MT5_DISCONNECT_COUNT', 0)})`\n"
    msg += f"AI: {ai_status} `({getattr(shared_state, 'AI_DISCONNECT_COUNT', 0)})`\n"
    msg += f"Discord: ✅ Online `({getattr(shared_state, 'DISCORD_DISCONNECT_COUNT', 0)})`\n"

    try:
        await channel.send(msg)
    except Exception as e:
        import logging
        logging.getLogger("System").error(f"❌ ส่ง Report ไม่ได้: {e}")

# ==========================================
# ⏰ ระบบ Report อัตโนมัติ (Task Loops)
# ==========================================
@tasks.loop(minutes=1)
async def scheduled_reports():
    global LAST_REPORT_HOUR
    
    # ถ้าบอทไม่ได้เปิดรันอยู่ ไม่ต้องส่ง Report
    if shared_state.BOT_STATE not in ["RUNNING", "COOLDOWN"]: 
        return
        
    now = datetime.now()
    
    # 🟢 ถ้าเข็มยาวชี้เลข 12 (นาทีที่ 00) และยังไม่ได้ส่งของชั่วโมงนี้
    if now.minute == 0 and now.hour != LAST_REPORT_HOUR:
        LAST_REPORT_HOUR = now.hour # จดไว้ว่าส่งของชั่วโมงนี้แล้ว
        
        if now.hour == 0:
            await send_end_day_report() 
            shared_state.MT5_DISCONNECT_COUNT = 0
            shared_state.AI_DISCONNECT_COUNT = 0
            shared_state.DISCORD_DISCONNECT_COUNT = 0
        elif now.hour == 12:
            await send_status_report("[Half-Day Report]")
        elif now.hour == 18:
            await send_status_report("[Normal Working Hours Report]")
        elif now.hour in [3, 6, 9, 15, 21]:
            await send_status_report("[Status Report]")

@scheduled_reports.before_loop
async def before_scheduled_reports():
    await bot.wait_until_ready()

# ==========================================
# 💬 ระบบรับคำสั่ง (Discord Commands)
# ==========================================
@bot.command()
async def status(ctx):
    await send_status_report(title="[Manual Status Request]")

@bot.command()
async def start(ctx):
    import __main__
    if shared_state.BOT_STATE not in ["STOPPED", "PAUSED", "COOLDOWN"]:
        await ctx.send("⚠️ System is already running")
        return
        
    if hasattr(__main__, 'app_instance') and __main__.app_instance:
        __main__.app_instance.after(0, __main__.app_instance.start_bot)
        await ctx.send("▶️ **รับทราบ!** กำลังอัปเดตกลยุทธ์และเริ่มการทำงานครับ")
    else:
        await ctx.send("❌ ไม่สามารถติดต่อหน้าต่างโปรแกรมหลักได้")

@bot.command()
async def stop(ctx):
    if shared_state.BOT_STATE == "STOPPED":
        await ctx.send("⚠️ บอทหยุดทำงานอยู่แล้วครับ")
        return
        
    shared_state.BOT_STATE = "STOPPED"
    logging.getLogger("System").error("🛑 บอทถูกหยุดผ่าน Discord Command")
    await send_closing_report(ctx.channel)

@bot.command()
async def pause(ctx):
    shared_state.BOT_STATE = "COOLDOWN"
    shared_state.COOLDOWN_REMAINING = 30
    logging.getLogger("System").warning("⚠️ บอทถูกสั่ง Pause ผ่าน Discord (พัก 30 นาที)")
    await ctx.send("⏸️ **รับทราบ!** สั่งบอทเข้าสู่โหมดจำศีล 30 นาทีเรียบร้อยครับ")

@bot.command()
async def connection(ctx):
    ai_status = "✅ Online" if getattr(ai, 'AI_IS_ONLINE', False) else f"❌ Error {getattr(ai, 'AI_ERROR_CODE', '')}"
    mt5_status = "✅ Connected" if mt5.terminal_info() else "❌ Disconnected"
    
    msg = f"🔌 **[Connection Check]**\nMT5: {mt5_status}\nAI: {ai_status}\nDiscord: ✅ Online (Ping: {round(bot.latency * 1000)}ms)"
    await ctx.send(msg)

@bot.command()
async def restart(ctx):
    await ctx.send("🔄 **Restarting Please Wait.....")
    
    import __main__
    if hasattr(__main__, 'app_instance') and __main__.app_instance:
        # สะกิดให้หน้าต่าง UI ทำการรีสตาร์ทตัวเอง
        __main__.app_instance.after(0, __main__.app_instance.restart_system)
    else:
        await ctx.send("❌ ไม่สามารถติดต่อหน้าต่างโปรแกรมหลักได้")

@bot.event
async def on_command_error(ctx, error):
    import logging
    # 🟢 บันทึก Error จาก Discord ลงไฟล์ Log (System)
    logging.getLogger("System").error(f"❌ Discord Command Error: {error}")
    
    # แจ้งเตือนกลับในแชท Discord เพื่อให้เรารู้ตัว
    try:
        if isinstance(error, commands.CommandNotFound):
            # กรณีพิมพ์คำสั่งผิด เช่น !stoppp หรือบอทยังโหลดคำสั่งไม่เสร็จ
            await ctx.send(f"❓ ไม่พบคำสั่งนี้ครับพี่: `{ctx.message.content}`")
        else:
            # กรณี Error อื่นๆ เช่น Permission หรือ API ล่ม
            await ctx.send(f"⚠️ Discord Command Error: `{error}`")
    except:
        pass

async def change_risk(ctx, level):
    import __main__
    import logging
    import shared_state
    
    shared_state.CURRENT_RISK_LEVEL = level
    logging.getLogger("System").warning(f"⚠️ เปลี่ยนระดับความเสี่ยงผ่าน Discord เป็น Level {level} (กำลังอัปเดตแผนใหม่...)")
    
    # บังคับอัปเดตแผน AI ทันที
    if hasattr(__main__, 'app_instance') and __main__.app_instance:
        __main__.app_instance.after(0, __main__.app_instance.start_bot)
        await ctx.send(f"🚀 **Risk Level {level} Active!** กำลังคำนวณเป้าหมาย RSI ชุดใหม่ครับพี่")
    else:
        await ctx.send(f"✅ ปรับ Risk Level เป็น {level} เรียบร้อย (รออัปเดตรอบถัดไป)")

@bot.event
async def on_disconnect():
    shared_state.DISCORD_DISCONNECT_COUNT = getattr(shared_state, 'DISCORD_DISCONNECT_COUNT', 0) + 1 # 🟢 บวกแต้ม Discord
    logging.getLogger("System").warning("📡 ขาดการติดต่อจาก Discord กำลังพยายามเชื่อมต่อใหม่...")

@bot.event
async def on_resumed():
    # 🟢 แจ้งเตือนเมื่อกลับมาต่อได้
    logging.getLogger("System").info("✅ เชื่อมต่อ Discord กลับมาสำเร็จแล้ว")

@bot.command()
async def risk1(ctx): await change_risk(ctx, 1)

@bot.command()
async def risk2(ctx): await change_risk(ctx, 2)

@bot.command()
async def risk3(ctx): await change_risk(ctx, 3)

@bot.command()
async def risk4(ctx): await change_risk(ctx, 4)

@bot.command()
async def risk5(ctx): await change_risk(ctx, 5)