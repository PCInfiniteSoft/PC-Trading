import discord
import MetaTrader5 as mt5
import asyncio
import os
import sys
import logging
import shared_state
import ai_engine as ai
import trade_manager as tm
from bot_config import *
from discord.ext import commands, tasks
from datetime import datetime, time

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ==========================================
# 📊 ฟังก์ชันคำนวณและดึงข้อมูล P/L วันนี้
# ==========================================
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
async def send_startup_report():
    channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"

    msg = f"📊 **[Starting PCTrading 2.0]**\n⏰ **[Time: {now_str}]**\n-----------------------------------\n"
    
    for s in SYMBOLS_CONFIG:
        msg += build_symbol_status(s) + "\n"

    ai_status = "✅ Online" if getattr(ai, 'AI_IS_ONLINE', False) else f"❌ Error {getattr(ai, 'AI_ERROR_CODE', '')}"
    
    msg += f"🔌 **System Connection**\nMT5: ✅ Connected\nAI: {ai_status}\nDiscord: ✅ Online\n-----------------------------------\n"
    msg += f"💵 **Current Balance:** `{balance} USD`"
    
    await channel.send(msg)

async def send_closing_report(channel=None):
    if not channel:
        channel = bot.get_channel(int(REPORT_CHANNEL_ID))
    if not channel: return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"

    msg = f"📊 **[Closing PCTrading 2.17]**\n⏰ **[Time: {now_str}]**\n-----------------------------------\n"
    
    total_deals = 0
    total_profit = 0.0

    for s in SYMBOLS_CONFIG:
        deals = get_today_deals(s)
        count = len(deals)
        profit = sum(d.profit for d in deals)
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

    acc = mt5.account_info()
    balance = f"{acc.balance:,.2f}" if acc else "0.00"
    deals = get_today_deals()
    total_profit = sum(d.profit for d in deals)
    total_icon = "🟢" if total_profit >= 0 else "🔴"

    msg = f"📊 **{title}**\n-----------------------------------\n"
    for s in SYMBOLS_CONFIG:
        msg += build_symbol_status(s) + "\n"

    
    msg += f"💰 **Total P/L :**\n📦 Closed : `{len(deals)} Order`\n{total_icon} P/L : `{total_profit:.2f} USD`\n-----------------------------------\n"
    msg += f"💰 **Total Daily P/L :** `{total_profit:.2f} USD`\n💵 **Current Balance:** `{balance} USD`\n-----------------------------------\n"
    
    state = shared_state.BOT_STATE

    risk = shared_state.CURRENT_RISK_LEVEL
    msg += f"📊 **Current Risk Profile:** `Level {risk}/5`"

    if state == "RUNNING":
        msg += "✅ **Running**"
    elif state == "COOLDOWN":
        msg += f"⚠️ **Cooldown (Cooldown: {shared_state.COOLDOWN_REMAINING}m)**"
    else:
        msg += f"🛑 **Status :** `{state}`"

    await channel.send(msg)

# ==========================================
# ⏰ ระบบ Report อัตโนมัติ (Task Loops)
# ==========================================
@tasks.loop(minutes=30)
async def auto_report_job():
    if shared_state.BOT_STATE in ["RUNNING", "COOLDOWN"]:
        await send_status_report()

@tasks.loop(minutes=1)
async def half_day_report_job():
    now = datetime.now()
    if now.minute == 0 and now.hour in [6, 18]:
        part = "เช้า" if now.hour == 6 else "เย็น"
        await send_status_report(title=f"[Half-Day Report ({part})]")

@half_day_report_job.before_loop
async def before_half_day():
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