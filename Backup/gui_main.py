import customtkinter as ctk
import MetaTrader5 as mt5
import ai_engine as ai
import trade_manager as tm
import pygetwindow as gw
import advanced_indicators as adv
import system_logs as slogs
import system_utils as sutils
import shared_state
import json
import traceback
import threading, asyncio, os, sys, logging, time
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone
from discord.ext import tasks
from bot_config import *
from discord_manager import bot, auto_report_job, half_day_report_job, get_win_loss_text, send_startup_report

app_instance = None 

def save_web_status():
        status_data = {
            "state": shared_state.BOT_STATE,
            "balance": mt5.account_info().balance if mt5.account_info() else 0,
            "risk_level": shared_state.CURRENT_RISK_LEVEL,
            "symbols": {}
        }
        for s in SYMBOLS_CONFIG:
            status_data["symbols"][s] = {
                "price": mt5.symbol_info_tick(s).ask if mt5.symbol_info_tick(s) else 0,
                "regime": ai.STRATEGY_DATA[s]["regime"],
                "rsi": tm.get_rsi(s)
            }
    
        with open("bot_status.json", "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=4)

class DailyFileHandler(logging.FileHandler):
    def __init__(self, base_name, mode='a', encoding='utf-8', delay=False):
        # 1. ถอด .txt ออกถ้ามีติดมา จะได้ประกอบชื่อใหม่ได้เนียนๆ
        self.base_name = base_name.replace(".txt", "")
        self.current_date = datetime.now().strftime("%Y_%m_%d")
        
        # 2. ตั้งชื่อไฟล์แรกเริ่ม เช่น PCTrading_log_2026_04_25.txt
        filename = f"{self.base_name}_{self.current_date}.txt"
        super().__init__(filename, mode, encoding, delay)

    def emit(self, record):
        # 3. ก่อนจะเขียน Log ทุกบรรทัด ให้เช็คก่อนว่า "ข้ามวันหรือยัง?"
        new_date = datetime.fromtimestamp(record.created).strftime("%Y_%m_%d")
        
        if self.current_date != new_date:
            # ถ้าข้ามวันแล้ว (เช่น เที่ยงคืนปุ๊บ)
            self.current_date = new_date
            self.close() # ปิดไฟล์ของเมื่อวาน
            
            # สร้างและสลับไปเขียนไฟล์ของวันนี้แทน
            self.baseFilename = os.path.abspath(f"{self.base_name}_{self.current_date}.txt")
            self.stream = self._open()
            
        # 4. เขียน Log ลงไฟล์
        super().emit(record)

@bot.event
async def on_ready():
    logging.getLogger("System").info("✅ Discord Bot Online")
    if not trading_job.is_running(): trading_job.start()
    if not auto_report_job.is_running(): auto_report_job.start()
    if not half_day_report_job.is_running(): half_day_report_job.start()
    if mt5.terminal_info() is not None and app_instance is not None:
        app_instance.after(0, app_instance.start_bot) 

@tasks.loop(minutes=1)
async def trading_job():
    shared_state.SCAN_COUNT += 1

    if tm.check_daily_drawdown():
        return

    if shared_state.BOT_STATE in ["STOPPED", "WAITING", "DAY-OFF"]: 
        return
    
    if shared_state.BOT_STATE == "COOLDOWN":
        shared_state.COOLDOWN_REMAINING -= 1 
        if shared_state.COOLDOWN_REMAINING <= 0:
            logging.getLogger("System").warning("⏳ AI ตื่นจากจำศีล กำลังตรวจสอบตลาด...")
            for s in SYMBOLS_CONFIG:
                rates = mt5.copy_rates_from_pos(s, TIMEFRAME, 0, 15)
                prices = [r['close'] for r in rates]
                volumes = [r['tick_volume'] for r in rates]
                res = await ai.ai_check_cooldown(s, prices, volumes)
                if res['status'] == "Stabilized":
                    shared_state.BOT_STATE = "RUNNING"
                    shared_state.AI_RETRY_COUNT = 0 # ตื่นแล้วรีเซ็ตตัวนับ AI
                else:
                    shared_state.COOLDOWN_REMAINING = 30
                    return
        return

    if not mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV): return
    
    is_ai_update_turn = (shared_state.SCAN_COUNT % 5 == 1)
    
    if is_ai_update_turn and not ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = getattr(shared_state, 'AI_RETRY_COUNT', 0) + 1
        logging.getLogger("System").warning(f"⚠️ พยายามปลุก AI ให้ตื่น (รอบที่ {shared_state.AI_RETRY_COUNT}/5)...")
    elif ai.AI_IS_ONLINE:
        shared_state.AI_RETRY_COUNT = 0 

    if getattr(shared_state, 'AI_RETRY_COUNT', 0) >= 5:
        shared_state.BOT_STATE = "COOLDOWN"
        shared_state.COOLDOWN_REMAINING = 30
        shared_state.AI_RETRY_COUNT = 0 
        logging.getLogger("System").error("🚨 สัญญาณ AI ขาดหายเกิน 5 นาที! เข้าสู่โหมดจำศีลเพื่อความปลอดภัย")
        return

    has_spike = False
    for s in SYMBOLS_CONFIG:
        rsi = tm.get_rsi(s)
        if rsi is None: continue
        
        if not tm.is_safe_trading_time(s):
            continue
            
        is_volatile = tm.check_volatility(s, ai.STRATEGY_DATA[s]["threshold"])
        
        if is_ai_update_turn or is_volatile or not ai.AI_IS_ONLINE:
            if is_volatile: 
                has_spike = True
                shared_state.FAST_GEAR_COUNT += 1
            
            await ai.ai_update_strategy(s, get_win_loss_text())
            shared_state.TRADE_LAYERS[s] = {"buy": [False]*5, "sell": [False]*5}

        if shared_state.FAST_GEAR_COUNT >= 5:
            shared_state.BOT_STATE = "COOLDOWN"
            shared_state.COOLDOWN_REMAINING = 30
            shared_state.FAST_GEAR_COUNT = 0
            logging.getLogger("System").error("🚨 Circuit Breaker Active! ตลาดผันผวนเกินไป พัก 30 นาที")
            return

        strat = ai.STRATEGY_DATA[s]

        # โชว์ Log บนหน้าจอเฉพาะรอบ 5 นาที (กันหน้าจอลายตาเกินไป)
        if is_ai_update_turn:
            buy_target = strat['buy'][0] if strat.get('buy') else '--'
            logging.getLogger(s).info(f"📊 RSI: {rsi:.2f} | เป้าซื้อ AI: < {buy_target} | ตลาด: {strat['regime']}")
        
        safe_buy = strat['buy'][0] if strat.get('buy') else 0
        if safe_buy > 100:
            logging.getLogger("System").error(f"🚨 บล็อกการเทรด {s}: AI ส่งเป้าหมายราคา ({safe_buy}) แทน RSI!")
            continue

        for i in range(5):
            # 🟢 ฝั่ง BUY: ถ้า AI ออนไลน์ ถึงจะคำนวณและซื้อ
            if rsi < strat['buy'][i] and not shared_state.TRADE_LAYERS.get(s, {}).get("buy", [False]*5)[i]:
                if ai.AI_IS_ONLINE:
                    tick = mt5.symbol_info_tick(s)
                    st_data = adv.get_3_indicators(s) 
                    ans = await ai.ai_analysis(s, tick.ask, rsi, st_data)
                    
                    if ans.get('decision') == "BUY":
                        if tm.place_order(s, "BUY", tick.ask, rsi, f"L{i+1}:{ans['reason']}"):
                            shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["buy"][i] = True

            # 🟢 ฝั่ง SELL: ตัดกำไรได้เลยไม่ต้องรอ AI
            if mt5.positions_get(symbol=s) and rsi > strat['sell'][i] and not shared_state.TRADE_LAYERS.get(s, {}).get("sell", [False]*5)[i]:
                if tm.close_one_order(s):
                    shared_state.TRADE_LAYERS.setdefault(s, {"buy":[False]*5, "sell":[False]*5})["sell"][i] = True

    save_web_status()

@trading_job.error
async def trading_job_error(exc):
    import traceback
    error_msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logging.getLogger("System").error(f"🚨 [CRITICAL BUG] ระบบเทรดช็อตกระทันหัน:\n{error_msg}")


# ==========================================
# 🟢 2. GUI Application
# ==========================================
class PCTradingApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        global app_instance
        app_instance = self
        
        self.title("PC Trading 2.1.7")
        self.geometry("700x700")
        ctk.set_appearance_mode("dark")
        self.setup_ui()
        self.setup_logging()
        
        if mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV):
            logging.getLogger("System").info("✅ เชื่อมต่อ MT5 สำเร็จแล้ว")
            def hide_mt5():
                for keyword in ["Exness", "MT5"]:
                    for window in gw.getWindowsWithTitle(keyword):
                        window.minimize()
                        
            self.after(10000, hide_mt5)
    
        else:
            logging.getLogger("System").error("❌ เชื่อมต่อ MT5 ล้มเหลว โปรดตรวจสอบว่าเปิดโปรแกรม MT5 ไว้หรือไม่")
            
        threading.Thread(target=lambda: bot.run(TOKEN), daemon=True).start()
        self.update_dashboard()
        self.process_log_queue()
        self.protocol("WM_DELETE_WINDOW", lambda: [self.on_closing(), os._exit(0)])

    def setup_ui(self):

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=10, fill="x")
        self.lbl_state = ctk.CTkLabel(header, text="STOPPED", font=("Arial", 26, "bold"), text_color="red")
        self.lbl_state.pack()
        
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="▶️ START", fg_color="#2ecc71", hover_color="#27ae60", width=140, command=self.start_bot).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="🛑 STOP", fg_color="#e74c3c", hover_color="#c0392b", width=140, command=self.stop_bot).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="⏸️ PAUSE", fg_color="#f39c12", hover_color="#d35400", width=120, command=self.pause_bot).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="🔄 RESTART", fg_color="#3498db", hover_color="#2980b9", width=120, command=self.restart_system).pack(side="left", padx=5)

        stats_frame = ctk.CTkFrame(self, fg_color="#2c3e50")
        stats_frame.pack(pady=5, padx=20, fill="x")
        
        row1 = ctk.CTkFrame(stats_frame, fg_color="transparent")
        row1.pack(pady=(8, 2), anchor="center")
        
        self.lbl_sys_discord = ctk.CTkLabel(row1, text="🟣 Discord: Offline", text_color="gray", font=("Arial", 12, "bold"))
        self.lbl_sys_discord.pack(side="left", padx=15)
        self.lbl_sys_mt5 = ctk.CTkLabel(row1, text="🔵 MT5: Offline", text_color="gray", font=("Arial", 12, "bold"))
        self.lbl_sys_mt5.pack(side="left", padx=15)
        self.lbl_sys_ai = ctk.CTkLabel(row1, text="🧠 AI: Standby", text_color="gray", font=("Arial", 12, "bold"))
        self.lbl_sys_ai.pack(side="left", padx=15)
        
        row2 = ctk.CTkFrame(stats_frame, fg_color="transparent")
        row2.pack(pady=(2, 8), anchor="center")
        
        self.lbl_gear = ctk.CTkLabel(row2, text="⚙️ Gear: 10m", font=("Arial", 12, "bold"))
        self.lbl_gear.pack(side="left", padx=15)
        self.lbl_winloss = ctk.CTkLabel(row2, text="🏆 W/L: Loading...", font=("Arial", 12, "bold"), text_color="#3498db")
        self.lbl_winloss.pack(side="left", padx=15)
        self.lbl_balance = ctk.CTkLabel(row2, text="💵 Balance: 0.00 USD", font=("Arial", 12))
        self.lbl_balance.pack(side="left", padx=15)

        # Asset Cards
        info_container = ctk.CTkFrame(self, fg_color="transparent")
        info_container.pack(pady=10, padx=10, fill="x")
        self.asset_ui = {}
        for i, s in enumerate(SYMBOLS_CONFIG):
            f = ctk.CTkFrame(info_container, border_width=2, border_color="#34495e")
            f.pack(side="left", expand=True, fill="both", padx=15, pady=5)
            ctk.CTkLabel(f, text=s, font=("Arial", 20, "bold"), text_color="#3498db").pack(pady=8)
            
            l_price = ctk.CTkLabel(f, text="Price: 0.00", font=("Consolas", 18, "bold"), text_color="#ffffff")
            l_price.pack(pady=2)
            l_market = ctk.CTkLabel(f, text="Market: Checking...", font=("Arial", 12, "bold"))
            l_market.pack(pady=2)
            l_regime = ctk.CTkLabel(f, text="Regime: N/A", font=("Arial", 14))
            l_regime.pack()
            l_rsi = ctk.CTkLabel(f, text="RSI: N/A", font=("Arial", 14))
            l_rsi.pack(pady=2)
            l_target = ctk.CTkLabel(f, text="Buy < -- | Sell > --", font=("Arial", 13), text_color="#2ecc71")
            l_target.pack(pady=5)
            l_wl_pl = ctk.CTkLabel(f, text="🏆 W:0 L:0 | 💰 P/L: 0.00 USD", font=("Arial", 12, "bold"), text_color="#3498db")
            l_wl_pl.pack(pady=2)
            l_thresh = ctk.CTkLabel(f, text="Volatility Threshold: 0.00", font=("Arial", 11), text_color="#95a5a6")
            l_thresh.pack(pady=5)

            self.asset_ui[s] = {"price": l_price, "market": l_market, "regime": l_regime, "rsi": l_rsi, "target": l_target, "thresh": l_thresh, "wl_pl": l_wl_pl, "prev_price": 0.0}

            if i == 0:
                risk_frame = ctk.CTkFrame(info_container, fg_color="transparent")
                risk_frame.pack(side="left", padx=10)
                
                ctk.CTkLabel(risk_frame, text="High", font=("Arial", 12, "bold")).pack()
                
                self.risk_slider = ctk.CTkSlider(
                    risk_frame, 
                    from_=1, to=5, 
                    number_of_steps=4, 
                    orientation="vertical", 
                    height=180,
                    command=self.on_risk_slider_change
                )
                self.risk_slider.set(shared_state.CURRENT_RISK_LEVEL)
                self.risk_slider.pack(pady=5)
                
                ctk.CTkLabel(risk_frame, text="Low", font=("Arial", 12, "bold")).pack()

        # Log Box สีดำ
        self.log_box = ctk.CTkTextbox(self, state="disabled", font=("Consolas", 13), fg_color="#000000")
        self.log_box.pack(pady=10, padx=20, fill="both", expand=True)
        self.log_box.tag_config("SUCCESS", foreground="#00ff00")
        self.log_box.tag_config("WARNING", foreground="#ffcc00")
        self.log_box.tag_config("ERROR", foreground="#ff4d4d")
        self.log_box.tag_config("DEFAULT", foreground="#ffffff")

    def setup_logging(self):
        if not os.path.exists("Logs"):
            os.makedirs("Logs")
        
        class GUIHandler(logging.Handler):
            def __init__(self, app_instance, log_name=""):
                super().__init__()
                self.app = app_instance
                self.log_name = f"[{log_name}] " if log_name else ""
            def emit(self, record):
                msg = self.format(record)
                display_msg = f"{self.log_name}{msg}"
                shared_state.log_queue.put((display_msg, record.levelname))

        class LogsFormatter(logging.Formatter):
            def format(self, record):
                msg = record.getMessage()
                if msg and msg[0] in ["📊", "✅", "❌", "💰", "🚨", "⚠️", "📰"]:
                    time_str = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
                    parts = msg.split(" ", 1)
                    if len(parts) == 2:
                        return f"{parts[0]} [{time_str}] {parts[1]}"
                    return f"{msg} [{time_str}]"
                elif msg and msg[0] == "🔄":
                    return msg 
                time_str_default = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
                return f"[{time_str_default}] {msg}"

        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_gui = GUIHandler(self)
        root_gui.setFormatter(logging.Formatter('%(message)s'))
        root_logger.addHandler(root_gui)

        def create_rotating_logger(name, filename):
            logger = logging.getLogger(name)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            
            # 🟢 เลิกใช้ TimedRotatingFileHandler เปลี่ยนมาใช้ตัวที่เราสร้างเอง
            fh = DailyFileHandler(filename, encoding='utf-8')
            fh.setFormatter(LogsFormatter()) 
            logger.addHandler(fh)
            
            gh = GUIHandler(self, name)
            gh.setFormatter(logging.Formatter('%(message)s'))
            logger.addHandler(gh)
            
        create_rotating_logger("System", "Logs/PCTrading_log.txt")
        create_rotating_logger("BTCUSDm", "Logs/BTCUSDm_log.txt")
        create_rotating_logger("XAUUSDm", "Logs/XAUUSDm_log.txt")

        def global_exception_handler(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb)
                return
            error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            logging.getLogger("System").error(f"🚨 [FATAL ERROR] บอทช็อตตายกะทันหัน:\n{error_msg}")

        sys.excepthook = global_exception_handler

    def update_log(self, msg, levelname="INFO"):
        self.log_box.configure(state="normal")
        tag = "DEFAULT"
        if levelname == "ERROR" or "❌" in msg or "🚨" in msg or "🛑" in msg: tag = "ERROR"
        elif levelname == "WARNING" or "⏳" in msg or "⚠️" in msg: tag = "WARNING"
        elif levelname == "INFO" and ("✅" in msg or "🚀" in msg or "💰" in msg): tag = "SUCCESS"
        
        # เติมเวลาเข้าไปให้เป๊ะ
        time_str = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"{time_str} - {msg}\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def start_bot(self):
        # 🟢 แก้ไข: ให้กด Start ได้ถ้าสถานะไม่ใช่ RUNNING หรือ WAITING
        if shared_state.BOT_STATE in ["RUNNING", "WAITING"]: 
            return
            
        shared_state.BOT_STATE = "WAITING"
        logging.getLogger("System").warning("⏳ กำลังเริ่มการทำงานและอัปเดตกลยุทธ์.....")
        
        # ล้างค่า Cooldown ทิ้งทันทีเมื่อกด Start มือ
        shared_state.COOLDOWN_REMAINING = 0 
        shared_state.FAST_GEAR_COUNT = 0
        
        threading.Thread(target=self.initial_strategy_fetch, daemon=True).start()

    def initial_strategy_fetch(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 🟢 บังคับเกียร์ 2 นาทีตอนเริ่ม
        shared_state.CURRENT_LOOP_MINS = 2
        
        while shared_state.BOT_STATE == "WAITING":
            all_success = True
            for s in SYMBOLS_CONFIG:
                success = loop.run_until_complete(ai.ai_update_strategy(s, get_win_loss_text()))
                if not success: 
                    all_success = False
                    err_code = ai.AI_ERROR_CODE if hasattr(ai, 'AI_ERROR_CODE') and ai.AI_ERROR_CODE else "Timeout"
                    logging.getLogger("System").error(f"❌ โหลดแผน {s} ไม่ผ่าน (Err: {err_code}) จะลองใหม่ใน 2 นาที...")
                    
            if all_success:
                shared_state.BOT_STATE = "RUNNING"
                shared_state.TRADE_LAYERS = {s: {"buy": [False]*5, "sell": [False]*5} for s in SYMBOLS_CONFIG}
                
                # 🟢 โหลดผ่านแล้ว สลับเกียร์กลับเป็น 10 นาที
                shared_state.CURRENT_LOOP_MINS = 5
                if trading_job.is_running():
                    trading_job.change_interval(minutes=5)
                    
                logging.getLogger("System").info("✅ Strategy Ready! ปรับเป็น Gear 5m และเริ่มทำงานเต็มรูปแบบ")
                bot.loop.create_task(send_startup_report())
                break
            else:
                # ถ้ายืนยันว่า Failed ให้รอ 2 นาทีแล้ววนรอบใหม่ (Fast Boot Gear 2m)
                time.sleep(120)
                if shared_state.BOT_STATE != "WAITING": break

    def stop_bot(self): 
        shared_state.BOT_STATE = "STOPPED"
        logging.getLogger("System").error("🛑 บอทหยุดการทำงาน")

    def pause_bot(self):
        shared_state.BOT_STATE = "COOLDOWN"
        shared_state.COOLDOWN_REMAINING = 30
        logging.getLogger("System").warning("⚠️ บอทถูกสั่ง Pause ผ่านหน้าจอ UI (เข้าสู่โหมดพักฐาน 30 นาที)")    

    def update_dashboard(self):
        st = shared_state.BOT_STATE
        if st == "WAITING": txt, color = "Updating Strategy.....", "orange"
        elif st == "RUNNING": txt, color = "RUNNING", "green"
        elif st == "COOLDOWN": txt, color = f"COOLDOWN ({shared_state.COOLDOWN_REMAINING}m)", "orange"
        else: txt, color = "STOPPED", "red"
            
        self.lbl_state.configure(text=txt, text_color=color)
        self.lbl_gear.configure(text=f"⚙️ Gear: {shared_state.CURRENT_LOOP_MINS}m")
        self.lbl_winloss.configure(text=f"🏆 W/L: {get_win_loss_text()}")
        
        if bot.is_ready(): self.lbl_sys_discord.configure(text="🟣 Discord: Online", text_color="#9b59b6")
        
        acc = mt5.account_info()
        if acc: 
            self.lbl_sys_mt5.configure(text="🔵 MT5: Connected", text_color="#3498db")
            self.lbl_balance.configure(text=f"💵 Balance: {acc.balance:,.2f} USD")
        else:
            self.lbl_sys_mt5.configure(text="🔵 MT5: Disconnected", text_color="gray")
            
        if hasattr(ai, 'AI_IS_ONLINE'):
            if ai.AI_IS_ONLINE: self.lbl_sys_ai.configure(text="🧠 AI: Online", text_color="#2ecc71")
            else: self.lbl_sys_ai.configure(text=f"🧠 AI: Error {ai.AI_ERROR_CODE}", text_color="#e74c3c")

        for s in SYMBOLS_CONFIG:
            rsi = tm.get_rsi(s)
            data = ai.STRATEGY_DATA[s]
            ui = self.asset_ui[s]
            
            info = mt5.symbol_info(s)
            tick = mt5.symbol_info_tick(s)
            if info and tick:

                time_now_utc = datetime.now(timezone.utc).timestamp()
                tick_time_utc = tick.time 
                
                is_market_active = info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
                
                if is_market_active and (time.time() - tick.time < 3600): # อนุโลมให้เวลา Timezone ต่างกันได้นิดหน่อย แต่ถ้าข้ามวันคือปิดชัวร์
                    ui["market"].configure(text="Market: OPEN 🟢", text_color="#2ecc71")
                else:
                    ui["market"].configure(text="Market: CLOSED 🔴", text_color="#e74c3c")
                start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                deals = mt5.history_deals_get(start_of_day, datetime.now(), group=f"*{s}*")
            
                asset_p_l = 0.0
                asset_w = 0
                asset_l = 0
            
                if deals:
                    for d in deals:
                        if d.entry == mt5.DEAL_ENTRY_OUT: 
                            profit = d.profit + d.swap + d.commission
                            asset_p_l += profit
                            if profit > 0: asset_w += 1
                            elif profit < 0: asset_l += 1
                ui["wl_pl"].configure(text=f"🏆 W:{asset_w} L:{asset_l} | 💰 P/L: {asset_p_l:.2f} USD")            

            # 🟢 อัปเดตราคาแบบสดๆ พร้อมเปลี่ยนสี แดง/เขียว/ขาว
            tick = mt5.symbol_info_tick(s)
            if tick:
                curr_price = tick.ask
                prev_price = ui.get("prev_price", curr_price)
                
                if curr_price > prev_price: p_color = "#2ecc71" # เขียว
                elif curr_price < prev_price: p_color = "#e74c3c" # แดง
                else: p_color = ui["price"].cget("text_color") # สีเดิม
                    
                ui["price"].configure(text=f"Price: {curr_price:,.2f}", text_color=p_color)
                ui["prev_price"] = curr_price

            ui["regime"].configure(text=f"Regime: {data['regime']}")
            ui["rsi"].configure(text=f"RSI: {rsi:.2f}" if rsi else "RSI: N/A")
            if data['buy']: ui["target"].configure(text=f"Target: Buy < {data['buy'][0]} | Sell > {data['sell'][0]}")
            ui["thresh"].configure(text=f"Volatility Threshold: {data['threshold']:.2f}")
            
        self.after(2000, self.update_dashboard) # ดึงราคาเร็วขึ้นเป็นทุกๆ 2 วินาที

    def process_log_queue(self):

        while not shared_state.log_queue.empty():
            try:
                msg, levelname = shared_state.log_queue.get_nowait()
                self.update_log(msg, levelname)
            except:
                break
        self.after(100, self.process_log_queue)    

    def restart_system(self):
        logging.getLogger("System").warning("🔄 ได้รับคำสั่ง Restart กำลังเปิดระบบใหม่...")
        shared_state.BOT_STATE = "STOPPED"

        for keyword in ["Exness", "MT5", "MetaTrader 5"]:
            for window in gw.getWindowsWithTitle(keyword):
                try: window.close()
                except: pass
        mt5.shutdown()

        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            bat_path = os.path.join(current_dir, "PC Trading 2.17.bat")
            os.startfile(bat_path)
        except Exception as e:
            logging.getLogger("System").error(f"ไม่สามารถเปิดไฟล์ .bat ได้: {e}")

        # 3. พอเปิดตัวใหม่เสร็จปุ๊บ ค่อยสับสวิตช์ฆ่าตัวเก่าทิ้งทันที
        self.destroy()
        os._exit(0)

    def on_risk_slider_change(self, value):
        level = int(value)
        if level != shared_state.CURRENT_RISK_LEVEL:
            shared_state.CURRENT_RISK_LEVEL = level
            logging.getLogger("System").warning(f"⚠️ UI ปรับความเสี่ยงเป็น Level {level} (กำลังอัปเดตแผนใหม่...)")
            self.start_bot()

    def on_closing(self):
        logging.getLogger("System").warning("🛑 กำลังปิดโปรแกรมและหน้าต่าง MT5...")
        shared_state.BOT_STATE = "STOPPED"
        
        # ส่งรายงานก่อนปิด (ถ้าจำเป็น)
        try:
            import asyncio
            from discord_manager import send_closing_report
            asyncio.run_coroutine_threadsafe(send_closing_report(), bot.loop)
            import time
            time.sleep(1.5) # รอให้ส่งข้อความเสร็จ
        except: pass

        # ปิดหน้าต่าง MT5
        for keyword in ["Exness", "MT5", "MetaTrader 5"]:
            for window in gw.getWindowsWithTitle(keyword):
                try: window.close()
                except: pass
                    
        import MetaTrader5 as mt5
        mt5.shutdown()
        self.destroy()

if __name__ == "__main__":
    try:
        app = PCTradingApp()
        app.mainloop()
    except Exception as e:
        import traceback
        
        # ดักจับ Error แล้วเขียนลงไฟล์ CRASH_REPORT.txt ตรงๆ
        error_msg = traceback.format_exc()
        with open("CRASH_REPORT.txt", "w", encoding="utf-8") as f:
            f.write(error_msg)
            
        print("\n" + "🚨"*20)
        print("จับ ERROR ได้แล้ว! ข้อความตามด้านล่างนี้เลยครับ:")
        print(error_msg)
        print("🚨"*20)
        input("\nกด Enter เพื่อปิดหน้าต่างนี้...")