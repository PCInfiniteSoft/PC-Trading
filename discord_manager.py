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
    """ดึงยอดเทรดที่ปิดแล้วของวันนี้ จาก Database"""
    try:
        from database_manager import DB_NAME
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        if symbol:
            cursor.execute("SELECT COUNT(*), SUM(net_profit) FROM trade_history WHERE symbol=? AND exit_time LIKE ?", (symbol, f"{today_str}%"))
        else:
            cursor.execute("SELECT COUNT(*), SUM(net_profit) FROM trade_history WHERE exit_time LIKE ?", (f"{today_str}%",))
            
        res = cursor.fetchone()
        conn.close()
        
        count = res[0] if res and res[0] else 0
        profit = res[1] if res and res[1] else 0.0
        return count, profit
    except Exception as e:
        return 0, 0.0

async def send_closing_report(channel=None):
    if not channel:
        from bot_config import REPORT_CHANNEL_ID
        channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    import MetaTrader5 as mt5
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"

    msg = f"📊 **[Closing PCTrading 2.17]**\n⏰ **[Time: {now_str}]**\n-----------------------------------\n"
    
    total_deals = 0
    total_profit = 0.0
    
    from bot_config import SYMBOLS_CONFIG

    for s in SYMBOLS_CONFIG:
        deals = get_today_deals(s)
        count = len(deals) if deals else 0
        profit = sum(d.profit for d in deals) if deals else 0.0
        total_deals += count
        total_profit += profit
        
        icon = "🟢" if profit >= 0 else "🔴"
        msg += f"🔷 **{s}**\n📦 Closed : `{count} Order`\n{icon} P/L : `{profit:.2f} USD`\n-----------------------------------\n"

    total_icon = "🟢" if total_profit >= 0 else "🔴"
    msg += f"💰 **Total P/L:**\n📦 Closed : `{total_deals} Order`\n{total_icon} P/L : `{total_profit:.2f} USD`\n-----------------------------------\n"
    msg += f"💵 **Current Balance:** `{balance} USD`"
    
    await channel.send(msg)

async def send_status_report(title="[Status Report]"):
    channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return
    
    # 1. ดึงข้อมูลพอร์ตโดยรวม
    acc = mt5.account_info()
    curr_bal = acc.balance if acc else 0.0
    
    positions = mt5.positions_get()
    unrealized_pl = sum([(p.profit + p.swap) for p in positions]) if positions else 0.0
    total_closed_count, total_net_profit = get_today_db_stats()
    
    # 2. เริ่มสร้างข้อความ
    msg = f"📊 **{title}**\n========================\n"
    
    # 3. ลูปดึงข้อมูลแต่ละคู่เงิน
    for symbol, strat in ai.STRATEGY_DATA.items():
        if not strat: continue
        
        # เช็ค Market Open/Close
        tick = mt5.symbol_info_tick(symbol)
        is_open = "🟢" if tick and (datetime.now().timestamp() - tick.time < 300) else "🔴"
        
        # ดึง RSI
        rsi_text = "N/A"
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 15)
        if rates is not None and len(rates) > 0:
            prices = [r['close'] for r in rates]
            rsi_val = adv.calculate_rsi(prices)
            if rsi_val: rsi_text = f"{rsi_val[-1]:.2f}"
            
        # ดึงสถิติรายคู่เงิน
        sym_count, sym_profit = get_today_db_stats(symbol)
        
        msg += f"{is_open} **{symbol}** `[{strat.get('regime', 'N/A')}]`\n"
        msg += f"🛡️ Risk : `{shared_state.CURRENT_RISK_LEVEL}/5`\n"
        msg += f"📈 Current RSI : `{rsi_text}`\n"
        msg += f"📦 Trades : `{sym_profit:+.2f} USD` `[{sym_count} Orders]`\n"

    msg += "========================\n"
    
    # 4. สรุปยอดรวม
    unrealized_icon = "🟢" if unrealized_pl >= 0 else "🔴"
    net_icon = "🟢" if total_net_profit >= 0 else "🔴"
    
    msg += f"💰 **Today's P/L (Unrealized) :** `{unrealized_icon} {unrealized_pl:+.2f} USD`\n"
    msg += f"📦 Closed Trades : `{total_closed_count} Orders`\n"
    msg += f"**Net Profit :** `{net_icon} {total_net_profit:+.2f} USD`\n"
    msg += f"💵 **Current Balance:** `{curr_bal:.2f} USD`\n"
    msg += "========================\n"
    
    # 5. สรุปสถานะระบบ
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
            await send_status_report("[End-Day Report]")
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
    # 🟢 แจ้งเตือนแบบสะอาดๆ เมื่อเน็ตหลุด
    import shared_state
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