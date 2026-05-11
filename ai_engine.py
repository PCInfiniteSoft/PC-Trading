"""
PC Trading — AI Engine
Agent names:
  DIRECTOR  (was Agent 0) — Macro bias + allowed direction, runs every 4h or on ATR spike
  ANALYST   (was Agent 2) — Entry scoring 0-12pts using Supertrend + SMC + RSI + SCOUT bonus
  GUARDIAN  (was Agent 3) — Risk gate: cooldown, trend align, spread (lives in risk_manager.py)
  SCOUT                   — MACD + EMA pre-filter, now a score modifier (+0/+1/+2) for ANALYST
  SENTINEL                — MT5 reconnect watchdog (lives in trade_manager.py)
"""

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
import advanced_indicators as adv

# ── Globals ───────────────────────────────────────────────────────
NEWS_CACHE      = []
LAST_NEWS_FETCH = 0
AI_IS_ONLINE    = True
AI_ERROR_CODE   = ""

# ATR spike threshold that forces an early DIRECTOR refresh (% of price)
ATR_SPIKE_THRESHOLD = 1.2   # if H4 ATR% > this, trigger DIRECTOR immediately

RISK_PROFILES = {
    1: "Level 1 (Low Risk): Strictly conservative. Require extreme oversold/overbought setups. Maximize safety. Rare trades.",
    2: "Level 2: Conservative. Focus on strong reversals only.",
    3: "Level 3 (Medium Risk): Balanced. Standard risk-reward.",
    4: "Level 4: Aggressive. Capitalize on minor pullbacks and short-term momentum.",
    5: "Level 5 (High Risk): Scalping mode. Enter on clear momentum shifts. High frequency but STILL require minimum 2 of 3 criteria confirmed.",
}

# ── OpenAI client ─────────────────────────────────────────────────
def _get_api_key():
    try:
        with open("settings.txt", "r", encoding="utf-8") as f:
            for line in f:
                if "AI_KEY" in line:
                    return line.split("=", 1)[1].strip().strip('"\'')
    except Exception as e:
        logging.getLogger("System").error(f"❌ อ่าน settings.txt ไม่ได้: {e}")
    return ""

client = AsyncOpenAI(api_key=_get_api_key())
STRATEGY_DATA = {s: {"buy": [0,0,0,0,0], "sell": [100,100,100,100,100],
                     "regime": "N/A", "threshold": 0.0} for s in SYMBOLS_CONFIG}


# ══════════════════════════════════════════════════════════════════
#  DIRECTOR prompt (was MACRO_DIRECTOR_PROMPT / Agent 0)
# ══════════════════════════════════════════════════════════════════

DIRECTOR_PROMPT = """
You are DIRECTOR, the Macro Strategist of a quantitative trading fund.
Analyze H4/D1 technical data AND upcoming economic events.

[Input Data]
- Asset:             {symbol}
- H4 Trend:          {h4_trend}
- D1 Trend:          {d1_trend}
- H4 ATR% of price:  {atr_pct_h4}%  (>0.8% = elevated volatility)
- Upcoming News (not yet passed): {news_data}

[Instructions]
1. The news list above contains ONLY upcoming events that have NOT passed yet.
   If news_data is empty or says "No high-impact news", set direction based on trend only.
2. Only set allowed_direction = NONE if a news event is within the NEXT 30 minutes.
3. Set direction based on H4/D1 trend:
   - Both UPTREND → BUY_ONLY
   - Both DOWNTREND → SELL_ONLY  
   - Mixed or SIDEWAY → BOTH
   - News within 30 min → NONE
4. Risk level: default 3. Only lower if ATR > 1.0% AND news is imminent.
   Never set risk below 3 unless direction is NONE.

Output MUST be strictly valid JSON:
{{
  "macro_bias":        "STRONG_BULLISH" | "STRONG_BEARISH" | "BULLISH" | "BEARISH" | "SIDEWAY" | "WAIT_FOR_NEWS",
  "allowed_direction": "BUY_ONLY" | "SELL_ONLY" | "BOTH" | "NONE",
  "global_risk_level": 3,
  "reason": "Short explanation (max 80 chars)"
}}
"""


# ══════════════════════════════════════════════════════════════════
#  DIRECTOR — ai_macro_analysis  [UPGRADE #5: ATR-triggered refresh]
# ══════════════════════════════════════════════════════════════════

async def ai_macro_analysis(symbol, h4_trend, d1_trend, news_data, atr_pct_h4=0.0):
    prompt = DIRECTOR_PROMPT.format(
        symbol=symbol, h4_trend=h4_trend, d1_trend=d1_trend,
        atr_pct_h4=round(atr_pct_h4, 3), news_data=news_data
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=30.0
        )
        macro_plan = json.loads(response.choices[0].message.content)

        if not hasattr(shared_state, 'MACRO_DATA'):
            shared_state.MACRO_DATA = {}

        shared_state.MACRO_DATA[symbol] = {
            "bias":             macro_plan.get("macro_bias", "SIDEWAY"),
            "allowed_direction": macro_plan.get("allowed_direction", "BOTH"),
            "global_risk":      macro_plan.get("global_risk_level", 3),
            "reason":           macro_plan.get("reason", "—"),
            "atr_pct_h4":       atr_pct_h4,
            "h4_trend":         h4_trend,
            "d1_trend":         d1_trend,
            "set_time":         datetime.now(),
        }

        if macro_plan.get("allowed_direction") == "NONE":
            shared_state.MACRO_DATA[symbol]["none_since"] = datetime.now()
        else:
            shared_state.MACRO_DATA[symbol].pop("none_since", None)

        new_risk = macro_plan.get("global_risk_level", 3)
        shared_state.CURRENT_RISK_LEVEL = max(new_risk, 3) if macro_plan.get("allowed_direction") != "NONE" else new_risk

        logging.getLogger("System").info(
            f"👑 [DIRECTOR] {symbol}: {shared_state.MACRO_DATA[symbol]['bias']} "
            f"→ {shared_state.MACRO_DATA[symbol]['allowed_direction']} "
            f"| Risk {shared_state.CURRENT_RISK_LEVEL} | {shared_state.MACRO_DATA[symbol]['reason']}"
        )
        return macro_plan

    except Exception as e:
        logging.getLogger("System").warning(f"⚠️ [DIRECTOR] Macro analysis error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  STRATEGY UPDATE — per-symbol parameters
# ══════════════════════════════════════════════════════════════════

async def ai_update_strategy(symbol, win_loss_stats="N/A"):
    global AI_IS_ONLINE, AI_ERROR_CODE

    if not tm.is_safe_trading_time(symbol):
        logging.getLogger(symbol).info(f"💤 {symbol} ตลาดปิด — หยุดส่ง ANALYST วิเคราะห์ชั่วคราว")
        return True

    # ── ดึง per-symbol config ──────────────────────────────────────
    sym_cfg = SYMBOLS_CONFIG.get(symbol, {})
    rsi_buy_buffer  = sym_cfg.get("rsi_buy_buffer", 0.0)
    rsi_sell_buffer = sym_cfg.get("rsi_sell_buffer", 0.0)
    min_atr_pct     = sym_cfg.get("min_atr_pct", 0.04)

    rates_m5 = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 100)
    if rates_m5 is None or len(rates_m5) < 50:
        return False

    import pandas as pd
    df = pd.DataFrame(rates_m5)

    # RSI (14)
    delta = df['close'].diff()
    up   = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rsi_now = round(100 - 100 / (1 + up.iloc[-1] / (down.iloc[-1] + 1e-10)), 2)

    # ATR (14) and volatility %
    df['tr']  = pd.concat([df['high']-df['low'],
                            (df['high']-df['close'].shift()).abs(),
                            (df['low'] -df['close'].shift()).abs()], axis=1).max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    atr_now  = round(df['atr'].iloc[-1], 5)
    price    = df['close'].iloc[-1]
    atr_pct  = round(atr_now / price * 100, 3) if price > 0 else 0

    # Bollinger Bands (20, 2)
    df['sma20']  = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    bb_upper = round(df['sma20'].iloc[-1] + 2 * df['bb_std'].iloc[-1], 5)
    bb_lower = round(df['sma20'].iloc[-1] - 2 * df['bb_std'].iloc[-1], 5)
    bb_pct   = round((price - bb_lower) / max(bb_upper - bb_lower, 1e-8) * 100, 1)

    # MACD (12/26/9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    macd_val = round((ema12 - ema26).iloc[-1], 5)
    macd_sig = round((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1], 5)
    macd_hist = round(macd_val - macd_sig, 5)

    # Recent high/low range
    high_20 = round(df['high'].tail(20).max(), 5)
    low_20  = round(df['low'].tail(20).min(), 5)

    macro = getattr(shared_state, 'MACRO_DATA', {}).get(symbol, {})

    try:
        news_list = await asyncio.wait_for(
            asyncio.to_thread(get_today_high_impact_news, [symbol]), timeout=20.0)
    except asyncio.TimeoutError:
        news_list = ["News Timeout"]
    except Exception:
        news_list = ["News Error"]
    news_text = " | ".join(news_list)

    is_news_window = getattr(shared_state, 'CURRENT_LOOP_MINS', 5) == 1
    risk_level     = shared_state.CURRENT_RISK_LEVEL
    risk_instr     = RISK_PROFILES.get(risk_level, RISK_PROFILES[3])

    # ── [PER-SYMBOL] BTC-specific prompt hints ────────────────────
    if "BTC" in symbol:
        symbol_hints = f"""
[BTC-Specific Rules]
- BTC is highly volatile. ATR% is typically 0.06–0.19% per M5 candle (much wider than XAU).
- RSI swings are wider for BTC. Add {rsi_buy_buffer} pts buffer to buy_levels (lower them by {rsi_buy_buffer}).
- Add {rsi_sell_buffer} pts buffer to sell_levels (raise them by {rsi_sell_buffer}).
- In TRENDING_UP regime: buy_levels should allow entry up to RSI ~50 on pullbacks.
- In RANGING regime: buy_levels around RSI 35-42, sell_levels around RSI 58-65.
- Min ATR% to trade: {min_atr_pct}% — if ATR% < {min_atr_pct}, widen levels further.
- Do NOT use tight levels like XAU. BTC requires wider RSI bands.
"""
    else:
        symbol_hints = f"""
[XAU-Specific Rules]
- XAU ATR% is typically 0.04–0.08% per M5 candle.
- RSI cycles are more predictable. Standard levels apply.
- Min ATR% to trade: {min_atr_pct}%.
"""

    prompt = f"""
You are the PC Trading strategy engine. Set RSI entry levels for {symbol} based on the indicators below.

[Current Market Indicators — M5 timeframe]
- Price:          {round(price, 5)}
- RSI (14):       {rsi_now}
- ATR (14):       {atr_now} ({atr_pct}% of price)
- BB Position:    {bb_pct}% (0=lower band, 100=upper band)
- MACD histogram: {macd_hist} ({'bullish' if macd_hist > 0 else 'bearish'})
- 20-bar range:   {low_20} – {high_20}
- Macro Bias:     {macro.get('bias', 'N/A')} | Allow: {macro.get('allowed_direction', 'BOTH')}
- W/L Stats:      {win_loss_stats}
- News:           {news_text}
{"🚨 NEWS WINDOW ACTIVE: Widen levels for safety." if is_news_window else ""}

[Risk Profile]
{risk_instr}

{symbol_hints}

[Rules]
1. buy_levels: 5 RSI values (descending) where BUY orders fire (e.g. [32,30,28,26,24])
2. sell_levels: 5 RSI values (ascending) where SELL orders fire (e.g. [68,70,72,74,76])
3. All values MUST be between 15 and 85. NEVER use 0 or 100.
4. Widen levels if ATR% > 0.6 (volatile). Tighten if ATR% < 0.3 (quiet).
5. regime: one of TRENDING_UP | TRENDING_DOWN | RANGING | PULLBACK | VOLATILE
6. spike_threshold: % price move per M5 candle that triggers volatility mode

Output STRICT valid JSON only:
{{
  "buy_levels": [5 floats],
  "sell_levels": [5 floats],
  "regime": "string",
  "spike_threshold": float,
  "tp_activation": float,
  "pullback_pct": float,
  "be_activation": float,
  "be_lock_profit": float,
  "max_spread": int
}}
"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional trading AI. Output valid JSON only. Never use values outside 15-85 for RSI levels."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=30.0
        )
        AI_IS_ONLINE = True
        AI_ERROR_CODE = ""

        data = json.loads(response.choices[0].message.content)

        STRATEGY_DATA[symbol]["buy"]          = sorted(data['buy_levels'],  reverse=True)
        STRATEGY_DATA[symbol]["sell"]         = sorted(data['sell_levels'])
        STRATEGY_DATA[symbol]["threshold"]    = float(data.get('spike_threshold', sym_cfg.get("threshold", 0.25)))
        STRATEGY_DATA[symbol]["atr_pct"]      = atr_pct
        STRATEGY_DATA[symbol]["bb_pct"]       = bb_pct
        STRATEGY_DATA[symbol]["tp_activation"]  = float(data.get('tp_activation', 3.0))
        STRATEGY_DATA[symbol]["pullback_pct"]   = float(data.get('pullback_pct', 0.30))
        STRATEGY_DATA[symbol]["be_activation"]  = float(data.get('be_activation', 1.50))
        STRATEGY_DATA[symbol]["be_lock_profit"] = float(data.get('be_lock_profit', 0.20))
        STRATEGY_DATA[symbol]["max_spread"]     = int(data.get('max_spread', sym_cfg.get("max_spread_override", 5000)))

        # ── Regime stability lock ──────────────────────────────────
        new_regime = data['regime']
        stab = shared_state.REGIME_STABILITY.setdefault(symbol, {
            "current": new_regime, "count": 1,
            "pending": new_regime, "pending_count": 1
        })

        if new_regime == stab["current"]:
            stab["count"] += 1
            stab["pending"] = new_regime
            stab["pending_count"] = 1
        elif new_regime == stab["pending"]:
            stab["pending_count"] += 1
            if stab["pending_count"] >= shared_state.REGIME_MIN_CONFIRMATIONS:
                old = stab["current"]
                stab["current"] = new_regime
                stab["count"]   = stab["pending_count"]
                logging.getLogger(symbol).info(
                    f"📊 [REGIME] เปลี่ยน {old} → {new_regime} "
                    f"(confirmed {stab['pending_count']}x)")
        else:
            stab["pending"]       = new_regime
            stab["pending_count"] = 1

        STRATEGY_DATA[symbol]["regime"] = stab["current"]

        logging.getLogger(symbol).info(
            f"🔄 [STRATEGY] {symbol} | RSI: {rsi_now} | ATR%: {atr_pct} | "
            f"Regime: {STRATEGY_DATA[symbol]['regime']} "
            f"(raw: {new_regime}) | "
            f"Buy[0]: {STRATEGY_DATA[symbol]['buy'][0]} | "
            f"Sell[0]: {STRATEGY_DATA[symbol]['sell'][0]}"
        )
        return True

    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger("System").warning(f"⚠️ [STRATEGY] Error ({symbol}): {e}")
        return False


# ══════════════════════════════════════════════════════════════════
#  ANALYST — ai_analysis  [FIX: per-symbol score offset]
# ══════════════════════════════════════════════════════════════════

async def ai_analysis(symbol, price, rsi, st_data):
    global AI_IS_ONLINE, AI_ERROR_CODE

    # ── ดึง per-symbol score offset ───────────────────────────────
    sym_cfg = SYMBOLS_CONFIG.get(symbol, {})
    score_offset = sym_cfg.get("analyst_score_offset", 0)

    risk = getattr(shared_state, 'CURRENT_RISK_LEVEL', 3)
    if risk == 5:   base_trigger = 4
    elif risk == 4: base_trigger = 5
    elif risk == 3: base_trigger = 6
    elif risk == 2: base_trigger = 7
    else:           base_trigger = 8

    # ปรับ trigger score ตาม symbol (BTC ลดลง 1 เพื่อผ่อนปรน)
    trigger_score = max(4, base_trigger + score_offset)

    order_type = "BUY" if rsi < 50 else "SELL"
    scout = adv.get_scout_score(symbol, order_type)
    scout_bonus = scout["score"]

    ob = st_data.get("ob", {"type": "None"})
    zone_score = adv.score_zone_proximity(price, ob, order_type)

    # ── BTC-specific prompt hint ───────────────────────────────────
    if "BTC" in symbol:
        btc_hint = (
            f"\n[BTC Note] BTC is more volatile than XAU. "
            f"Zone scoring may be lower due to wider price swings. "
            f"Trigger score is {trigger_score} (1 lower than XAU default). "
            f"RSI momentum and trend alignment carry more weight for BTC entries."
        )
    else:
        btc_hint = ""

    prompt = (
        f"Symbol {symbol} | Price {price} | RSI {rsi:.2f} | Regime {STRATEGY_DATA[symbol]['regime']}\n"
        f"Technical indicators:\n"
        f"  H1 Supertrend:  {st_data['supertrend_h1']}\n"
        f"  M15 Supertrend: {st_data['supertrend']}\n"
        f"  SMC Zone:       {st_data['ob_zone']}\n"
        f"  Chandelier:     Long stop {st_data['long_stop']} | Short stop {st_data['short_stop']}\n"
        f"  SCOUT pre-filter: {scout['reason']}\n"
        f"{btc_hint}\n"
        f"\n"
        f"[ANALYST SCORING SYSTEM — Max 12 points]\n"
        f"BUY criteria:\n"
        f"  1. H1 Supertrend = UPTREND 🟢           → +4 pts\n"
        f"  2. Price inside/near Demand Zone          → +4 pts (inside) or +2 pts (within 0.3%)\n"
        f"  3. RSI < 40                               → +2 pts\n"
        f"  SCOUT bonus (MACD+EMA alignment)          → +0/+1/+2 pts (already computed: +{scout_bonus})\n"
        f"SELL criteria:\n"
        f"  1. H1 Supertrend = DOWNTREND 🔴           → +4 pts\n"
        f"  2. Price inside/near Supply Zone           → +4 pts (inside) or +2 pts (within 0.3%)\n"
        f"  3. RSI > 60                                → +2 pts\n"
        f"  SCOUT bonus                                → +{scout_bonus} pts\n"
        f"\n"
        f"NOTE: Zone proximity score already determined = {zone_score} pts. Add this to your tally.\n"
        f"NOTE: SCOUT bonus already determined = {scout_bonus} pts. Add this to your tally.\n"
        f"\n"
        f"🔴 TRIGGER RULE: decision MUST be BUY/SELL (not HOLD) only if total score >= {trigger_score}.\n"
        f"   [UPGRADE #2] RSI alone (2pts) is NEVER enough to trigger — minimum 2 criteria required.\n"
        f"\n"
        f"Output JSON ONLY: {{\"score\": int, \"decision\": \"BUY/SELL/HOLD\", \"reason\": \"string\"}}"
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are ANALYST, a professional trading AI sniper. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=20.0
        )
        AI_IS_ONLINE = True
        AI_ERROR_CODE = ""
        result = json.loads(response.choices[0].message.content)

        logging.getLogger(symbol).info(
            f"🎯 [ANALYST] Score: {result.get('score')}/12 | "
            f"SCOUT: +{scout_bonus} | Zone: +{zone_score} | "
            f"Decision: {result.get('decision')} | {result.get('reason', '')}"
        )
        return result

    except Exception as e:
        AI_IS_ONLINE = False
        AI_ERROR_CODE = re.search(r"\d{3}", str(e)).group() if re.search(r"\d{3}", str(e)) else "Err"
        logging.getLogger(symbol).warning(f"⚠️ [ANALYST] Error: {e}")
        return {"score": 0, "decision": "HOLD", "reason": "ANALYST offline (safety block)"}


# ══════════════════════════════════════════════════════════════════
#  Cooldown check
# ══════════════════════════════════════════════════════════════════

async def ai_check_cooldown(symbol, prices, volumes):
    prompt = (f"SENTINEL cooldown check for {symbol}. "
              f"Last 15 closes: {prices[-15:]}. Volumes: {volumes[-15:]}. "
              f"Is the market stable enough to resume trading? "
              f"JSON: {{\"status\": \"Stabilized\" | \"Volatile\", \"reason\": \"string\"}}")
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"status": "Volatile", "reason": "AI offline during cooldown check"}


# ══════════════════════════════════════════════════════════════════
#  News fetcher
# ══════════════════════════════════════════════════════════════════

HIGH_IMPACT_WIDE   = ["nonfarm", "nfp", "fomc", "fed rate", "interest rate decision"]
HIGH_IMPACT_MEDIUM = ["cpi", "ppi", "gdp", "unemployment", "retail sales", "pce"]

def get_news_window_minutes(title: str) -> tuple[int, int]:
    title_lower = title.lower()
    if any(k in title_lower for k in HIGH_IMPACT_WIDE):
        return (30, 30)
    elif any(k in title_lower for k in HIGH_IMPACT_MEDIUM):
        return (20, 20)
    else:
        return (5, 15)


def get_today_high_impact_news(symbols):
    global NEWS_CACHE, LAST_NEWS_FETCH
    if time.time() - LAST_NEWS_FETCH < 3600 and NEWS_CACHE:
        return NEWS_CACHE

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    try:
        scraper  = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        response = scraper.get(url, timeout=15)

        if response.status_code != 200:
            logging.getLogger("System").error(f"❌ [News] HTTP {response.status_code}")
            return [f"News Error {response.status_code}"]

        if "<?xml" not in response.text[:100] and "<weeklyevents>" not in response.text[:100]:
            logging.getLogger("System").warning("⚠️ [News] ForexFactory blocked from VPS")
            return ["News data unavailable (VPS Blocked)."]

        tree      = ET.fromstring(response.content)
        today_str = datetime.now().strftime("%m-%d-%Y")

        active_currencies = set()
        for s in symbols:
            if "BTC" in s or "XAU" in s:
                active_currencies.add("USD")

        news_list = []
        shared_state.TODAY_NEWS_TIMES  = []
        shared_state.NEWS_WINDOWS      = []

        for event in tree.findall('event'):
            date     = event.find('date').text
            impact   = event.find('impact').text
            currency = event.find('country').text
            title    = event.find('title').text
            time_str = event.find('time').text

            if impact in ['High', 'Medium', 'Low'] and currency in active_currencies:
                dbm.log_news_event(date, time_str, currency, impact, title)

                if date == today_str and impact == 'High':
                    news_list.append(f"[{time_str}] {currency}: {title}")

                    if time_str and time_str.lower() not in ["all day", "tentative"]:
                        try:
                            from datetime import timedelta
                            news_dt   = datetime.strptime(time_str, "%I:%M%p")
                            thai_time = news_dt + timedelta(hours=11)
                            t         = thai_time.time()

                            before_m, after_m = get_news_window_minutes(title)
                            start_dt = (thai_time - timedelta(minutes=before_m)).time()
                            end_dt   = (thai_time + timedelta(minutes=after_m)).time()
                            now_time = datetime.now().time()

                            if end_dt < now_time:
                                logging.getLogger("System").info(
                                    f"⏩ [News] ข้ามข่าวที่ผ่านไปแล้ว: {title} (จบ {end_dt})")
                                continue

                            shared_state.TODAY_NEWS_TIMES.append(t)
                            shared_state.NEWS_WINDOWS.append({
                                "title": title, "time": t,
                                "start": start_dt, "end": end_dt
                            })
                            logging.getLogger("System").info(
                                f"📰 [News] {title} — pause window: -{before_m}m/+{after_m}m"
                            )
                        except:
                            pass

        if news_list:
            NEWS_CACHE      = news_list
            LAST_NEWS_FETCH = time.time()
            logging.getLogger("System").info(f"📰 [News] พบข่าวกล่องแดงวันนี้ {len(news_list)} ข่าว")
        else:
            NEWS_CACHE      = ["No high-impact news today."]
            LAST_NEWS_FETCH = time.time()
            logging.getLogger("System").info("🔍 [News] ไม่มีข่าวกล่องแดงวันนี้")

        return NEWS_CACHE

    except Exception as e:
        logging.getLogger("System").error(f"⚠️ [News] ดึงข่าวล้มเหลว: {str(e).split(' ')[0]}")
        return ["News data unavailable."]
