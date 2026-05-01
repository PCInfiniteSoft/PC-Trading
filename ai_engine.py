import asyncio, json, re, logging
import shared_state
import requests
import cloudscraper
import time
import trade_manager as tm
import MetaTrader5 as mt5
import xml.etree.ElementTree as ET
import database_manager as dbm
from datetime import datetime
from bot_config import *
from openai import AsyncOpenAI
#from google import genai

NEWS_CACHE = []
LAST_NEWS_FETCH = 0
AI_IS_ONLINE = True
AI_ERROR_CODE = ""

RISK_PROFILES = {
    1: "Level 1 (Low Risk): Strictly conservative. Require extreme oversold/overbought setups. Maximize safety. Rare trades.",
    2: "Level 2: Conservative approach. Focus on strong reversals.",
    3: "Level 3 (Medium Risk): Balanced approach. Standard risk-reward.",
    4: "Level 4: Aggressive. Capitalize on minor pullbacks and short-term momentum.",
    5: "Level 5 (High Risk): Extremely aggressive scalping. Enter on slightest momentum shifts. High frequency."
}

def get_api_key_from_txt():
    try:
        with open("settings.txt", "r", encoding="utf-8") as file:
            for line in file:
                if "AI_KEY" in line:
                    return line.split("=")[1].strip().replace('"', '').replace("'", "")
    except Exception as e:
        logging.getLogger("System").error(f"❌ อ่านไฟล์ settings.txt ไม่ได้: {e}")
    return ""

client = AsyncOpenAI(api_key=get_api_key_from_txt())
STRATEGY_DATA = {s: {"buy": [0, 0, 0, 0, 0], "sell": [100, 100, 100, 100, 100], "regime": "N/A", "threshold": 0.0} for s in SYMBOLS_CONFIG}

async def ai_update_strategy(symbol, win_loss_stats="N/A"):
    global AI_IS_ONLINE, AI_ERROR_CODE
    
    if not tm.is_safe_trading_time(symbol):
        logging.getLogger(symbol).info(f"💤 ตลาด {symbol} ปิดพักผ่อน บอทหยุดส่ง AI วิเคราะห์ชั่วคราว...")
        return True 

    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 50)
    if rates is None or len(rates) == 0: return False

    history = [r['close'] for r in rates]

    # 🛡️ เกราะที่ 1: ป้องกันเว็บข่าวค้าง (บังคับตัดจบใน 20 วินาที)
    try:
        news_list = await asyncio.wait_for(asyncio.to_thread(get_today_high_impact_news, [symbol]), timeout=20.0)
    except asyncio.TimeoutError:
        news_list = ["News Timeout - VPS Blocked"]
        logging.getLogger("System").warning("⚠️ เว็บข่าวโหลดนานเกิน 20 วินาที (Timeout) ตัดจบเพื่อป้องกันบอทค้าง")
    except Exception as e:
        news_list = ["News Error"]
        import traceback
        error_detail = traceback.format_exc().splitlines()[-1] # ดึงบรรทัดที่พังจริงๆ มาโชว์
        logging.getLogger("System").error(f"❌ โค้ดดึงข่าวมีปัญหา: {error_detail}")

    news_text = " | ".join(news_list)
    
    prompt = (f"Analyze {symbol} (50 bars): {history}. Today's Stats: {win_loss_stats}. "
              f"🚨 LATEST HIGH-IMPACT NEWS: {news_text}. "
              f"Consider this news for market regime and volatility anticipation. "
              f"CRITICAL RULE: 'buy_levels' and 'sell_levels' MUST strictly be RSI values (between 0 and 100). DO NOT return actual price levels. "
              f"Also, analyze volatility to provide dynamic risk management parameters. "
              f"Provide JSON: {{"
              f"\"buy_levels\": [5 floats], \"sell_levels\": [5 floats], "
              f"\"regime\": \"string\", \"spike_threshold\": float, "
              f"\"tp_activation\": float, \"pullback_pct\": float, "
              f"\"be_activation\": float, \"be_lock_profit\": float"
              f"CRITICAL RULE: 'buy_levels' and 'sell_levels' MUST be realistic tradable RSI values (strictly between 15 and 85). "
              f"DO NOT output extreme impossible values like 0 or 100 to avoid trading. If the market is highly volatile, widen the levels (e.g., 20/80 or 25/75) but NEVER use 0 or 100. DO NOT return actual price levels. "
              f"}}")

    if getattr(shared_state, 'CURRENT_LOOP_MINS', 5) == 1:
        prompt += "\n\n🚨 [NEWS WINDOW ACTIVE]: We are currently within the 20-minute window of a high-impact news release! Expect extreme volatility and fakeouts. You MUST widen your RSI layers to be extremely safe, or set levels to impossible values (e.g., Buy 0, Sell 100) if it's too dangerous to trade."

    risk_level = shared_state.CURRENT_RISK_LEVEL
    risk_instruction = RISK_PROFILES.get(risk_level, RISK_PROFILES[3])
    prompt += f"\n\n[CRITICAL INSTRUCTION]: {risk_instruction}\nYou MUST adjust your 'buy_levels' and 'sell_levels' in the JSON strictly according to this risk profile."

    try:
        # 🛡️ เกราะที่ 2: ป้องกันเซิร์ฟเวอร์ AI ค้าง (บังคับตัดจบใน 30 วินาที)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional trading AI. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={ "type": "json_object" },
            timeout=30.0 # 🟢 สำคัญมาก! บังคับ AI ตอบภายใน 30 วิ
        )
        
        AI_IS_ONLINE, AI_ERROR_CODE = True, ""
        
        data = json.loads(response.choices[0].message.content)
        
        STRATEGY_DATA[symbol]["buy"] = sorted(data['buy_levels'], reverse=True)
        STRATEGY_DATA[symbol]["sell"] = sorted(data['sell_levels'])
        STRATEGY_DATA[symbol]["regime"] = data['regime']
        STRATEGY_DATA[symbol]["threshold"] = float(data.get('spike_threshold', 0.5))
        STRATEGY_DATA[symbol]["tp_activation"] = float(data.get('tp_activation', 3.0))
        STRATEGY_DATA[symbol]["pullback_pct"] = float(data.get('pullback_pct', 0.30))
        STRATEGY_DATA[symbol]["be_activation"] = float(data.get('be_activation', 1.50))
        STRATEGY_DATA[symbol]["be_lock_profit"] = float(data.get('be_lock_profit', 0.20))
        
        logging.getLogger(symbol).info(
            f"🔄 [STRATEGY 2.2] {symbol} | W/L: {win_loss_stats} | Threshold: {STRATEGY_DATA[symbol]['threshold']:.2f} | DynRisk: ON")
        return True

    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger("System").warning(f"⚠️ AI Strategy Error ({symbol}): {str(e)}")
        return False

async def ai_check_cooldown(symbol, prices, volumes):
    prompt = f"Cooldown Check for {symbol}. Last 30m Prices: {prices}, Volumes: {volumes}. Is market stable? JSON: {{\"status\": \"Stabilized/Volatile\", \"reason\": \"string\"}}"
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0 
        )
        return json.loads(response.choices[0].message.content)
    except: 
        return {"status": "Volatile", "reason": "AI Error during cooldown"}

async def ai_analysis(symbol, price, rsi, st_data):
    global AI_IS_ONLINE, AI_ERROR_CODE
    prompt = (
        f"Symbol {symbol} Price {price} RSI {rsi:.2f} Market {STRATEGY_DATA[symbol]['regime']}. "
        f"Technical: [Supertrend: {st_data['supertrend']}] | [SMC Zone: {st_data['ob_zone']}] | "
        f"[Chandelier Exit -> Long Stop: {st_data['long_stop']}, Short Stop: {st_data['short_stop']}]. "
        f"Based on RSI and these institutional levels, Decision BUY/HOLD? "
        f"JSON: {{\"decision\": \"BUY/HOLD\", \"reason\": \"string\"}}"
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0 # 🟢 กันค้าง
        )
        AI_IS_ONLINE, AI_ERROR_CODE = True, ""
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger("System").warning(f"⚠️ AI Offline ({AI_ERROR_CODE}) - บังคับอนุมัติออเดอร์ด้วยระบบ Fallback RSI")
        return {"decision": "HOLD", "reason": f"AI Error (Safety Block)"}
    
def get_today_high_impact_news(symbols):
    global NEWS_CACHE, LAST_NEWS_FETCH
    if time.time() - LAST_NEWS_FETCH < 3600 and NEWS_CACHE:
        return NEWS_CACHE
    
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        response = scraper.get(url, timeout=15)
        
        if response.status_code != 200:
            logging.getLogger("System").error(f"❌ [News Error] ไม่สามารถดึงข่าวได้ Code: {response.status_code}")
            return [f"News Error {response.status_code}"]

        if "<?xml" not in response.text[:100] and "<weeklyevents>" not in response.text[:100]:
            logging.getLogger("System").warning(f"⚠️ [News Blocked] ForexFactory บล็อกการเชื่อมต่อจาก VPS")
            return ["News data unavailable (VPS Blocked)."]
            
        tree = ET.fromstring(response.content)
        today_str = datetime.now().strftime("%m-%d-%Y")
        
        active_currencies = set()
        for s in symbols:
            if "BTC" in s: active_currencies.add("USD")
            if "XAU" in s: active_currencies.add("USD")
            
        news_list = []
        shared_state.TODAY_NEWS_TIMES = []
        
        for event in tree.findall('event'):
            date = event.find('date').text
            impact = event.find('impact').text
            currency = event.find('country').text
            title = event.find('title').text
            time_str = event.find('time').text
            
            if impact in ['High', 'Medium', 'Low'] and currency in active_currencies:

                dbm.log_news_event(date, time_str, currency, impact, title)
                
                if date == today_str and impact == 'High':
                    news_list.append(f"[{time_str}] {currency}: {title}")
                    
                    if time_str and time_str.lower() not in ["all day", "tentative"]:
                        try:
                            from datetime import timedelta
                            news_time_obj = datetime.strptime(time_str, "%I:%M%p")
                            thai_time = news_time_obj + timedelta(hours=11)
                            shared_state.TODAY_NEWS_TIMES.append(thai_time.time())
                        except: pass

        if news_list:
            NEWS_CACHE = news_list
            LAST_NEWS_FETCH = time.time()
            logging.getLogger("System").info(f"📰 [News Updated] พบข่าวกล่องแดงวันนี้ {len(news_list)} ข่าว")
            return news_list
        else:
            NEWS_CACHE = ["No high-impact news today."]
            LAST_NEWS_FETCH = time.time()
            logging.getLogger("System").info("🔍 [News Status] วันนี้ไม่มีข่าวกล่องแดง")
            return ["No high-impact news today."]
            
    except Exception as e:
        err_msg = str(e).split(' ')[0]
        logging.getLogger("System").error(f"⚠️ [News Error] ดึงข่าวล้มเหลว: {err_msg}")
        return ["News data unavailable."]