import MetaTrader5 as mt5
import pandas as pd

# ══════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════

def _calc_atr_chandelier(df, atr_period=14, chandelier_period=22, multiplier=3):
    """Shared ATR + Chandelier Exit — avoids duplicating this everywhere."""
    df = df.copy()
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift())
    df['tr2'] = abs(df['low']  - df['close'].shift())
    df['tr']  = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(atr_period).mean()
    high_n = df['high'].rolling(chandelier_period).max()
    low_n  = df['low'].rolling(chandelier_period).min()
    df['long_stop']  = high_n - df['atr'] * multiplier
    df['short_stop'] = low_n  + df['atr'] * multiplier
    return df


# ══════════════════════════════════════════════════════════════════
#  RSI
# ══════════════════════════════════════════════════════════════════

def calculate_rsi(prices, period=14):
    if len(prices) < period:
        return 50.0
    df    = pd.DataFrame(prices, columns=['close'])
    delta = df['close'].diff()
    up    = delta.clip(lower=0)
    down  = -1 * delta.clip(upper=0)
    ema_up   = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs  = ema_up / ema_down
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.fillna(100).iloc[-1], 2)


def rsi_cross_down(m5_slice, level: float = 50.0) -> bool:
    """True when RSI crosses down through `level` on the latest bar
    (previous RSI >= level, current RSI < level)."""
    closes = m5_slice["close"].tolist()
    if len(closes) < 16:  # need >= 16 so closes[:-1] has 15 bars >= RSI period 14
        return False
    rsi_prev = calculate_rsi(closes[:-1])
    rsi_now = calculate_rsi(closes)
    return bool(rsi_prev >= level and rsi_now < level)


# ══════════════════════════════════════════════════════════════════
#  [UPGRADE #4] Proper SMC Order Block detection
#  Last opposing candle before a strong impulse move
# ══════════════════════════════════════════════════════════════════

def _find_smc_order_block(df, impulse_multiplier=1.5, lookback=30):
    df = df.tail(lookback + 5).reset_index(drop=True)
    result = {"type": "None", "high": 0, "low": 0, "open": 0, "close": 0, "age": 0}

    for i in range(len(df) - 2, 1, -1):
        body_next = abs(df.loc[i, 'close'] - df.loc[i, 'open'])
        atr_val   = df.loc[i, 'atr']
        if atr_val <= 0:
            continue
        if body_next <= impulse_multiplier * atr_val:
            continue

        bullish_impulse = df.loc[i, 'close'] > df.loc[i, 'open']
        bearish_impulse = not bullish_impulse

        for j in range(i - 1, max(i - 6, 0), -1):
            prev_bull = df.loc[j, 'close'] > df.loc[j, 'open']
            prev_bear = not prev_bull

            if bullish_impulse and prev_bear:
                return {"type": "Demand", "high": round(df.loc[j,'high'],5),
                        "low": round(df.loc[j,'low'],5), "open": round(df.loc[j,'open'],5),
                        "close": round(df.loc[j,'close'],5), "age": len(df)-1-j}
            if bearish_impulse and prev_bull:
                return {"type": "Supply", "high": round(df.loc[j,'high'],5),
                        "low": round(df.loc[j,'low'],5), "open": round(df.loc[j,'open'],5),
                        "close": round(df.loc[j,'close'],5), "age": len(df)-1-j}
    return result


# ══════════════════════════════════════════════════════════════════
#  [UPGRADE #8] Partial zone proximity scoring for ANALYST
# ══════════════════════════════════════════════════════════════════

def score_zone_proximity(price, ob: dict, order_type: str) -> int:
    """Returns 0, 2, or 4 points based on proximity to Order Block."""
    if ob["type"] == "None":
        return 0
    order = order_type.upper()
    zone_high, zone_low = ob["high"], ob["low"]

    if order == "BUY" and ob["type"] == "Demand":
        if zone_low <= price <= zone_high:
            return 4   # inside zone
        elif price <= zone_high * 1.003:
            return 2   # within 0.3% above
        return 0

    if order == "SELL" and ob["type"] == "Supply":
        if zone_low <= price <= zone_high:
            return 4   # inside zone
        elif price >= zone_low * 0.997:
            return 2   # within 0.3% below
        return 0

    return 0


# ══════════════════════════════════════════════════════════════════
#  get_recent_m5_closes — feeds trend-sell predicates
# ══════════════════════════════════════════════════════════════════

def get_recent_m5_closes(symbol, bars=120):
    """Return the last `bars` M5 closes as a DataFrame with a 'close' column
    (most-recent bar last), shaped for the trend-sell predicates."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars)
    if rates is None or len(rates) == 0:
        return pd.DataFrame({"close": []})
    return pd.DataFrame({"close": [r["close"] for r in rates]})


# ══════════════════════════════════════════════════════════════════
#  get_3_indicators — feeds ANALYST
# ══════════════════════════════════════════════════════════════════

def get_3_indicators(symbol, timeframe=mt5.TIMEFRAME_M15):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 60)
    if rates is None or len(rates) < 50:
        return {"supertrend": "N/A", "supertrend_h1": "N/A",
                "ob_zone": "No Zone", "ob": {"type": "None"},
                "long_stop": 0, "short_stop": 0}

    df    = pd.DataFrame(rates)
    df    = _calc_atr_chandelier(df)
    curr  = df['close'].iloc[-1]
    ls    = df['long_stop'].iloc[-1]
    ss    = df['short_stop'].iloc[-1]
    st    = "UPTREND 🟢" if curr > ls else "DOWNTREND 🔴"

    ob = _find_smc_order_block(df)
    if ob["type"] == "Demand":
        ob_zone = f"Demand Zone {ob['low']:.5f}-{ob['high']:.5f} (age {ob['age']}b)"
    elif ob["type"] == "Supply":
        ob_zone = f"Supply Zone {ob['low']:.5f}-{ob['high']:.5f} (age {ob['age']}b)"
    else:
        ob_zone = "No Zone"

    # H1
    rates_h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 60)
    st_h1 = "N/A"
    if rates_h1 is not None and len(rates_h1) >= 50:
        df_h1 = _calc_atr_chandelier(pd.DataFrame(rates_h1))
        st_h1 = "UPTREND 🟢" if df_h1['close'].iloc[-1] > df_h1['long_stop'].iloc[-1] else "DOWNTREND 🔴"

    return {"supertrend": st, "supertrend_h1": st_h1,
            "ob_zone": ob_zone, "ob": ob,
            "long_stop": round(ls,5), "short_stop": round(ss,5)}


# ══════════════════════════════════════════════════════════════════
#  get_macro_trends — feeds DIRECTOR
#  [UPGRADE #5] Returns atr_pct_h4 for intraday regime detection
# ══════════════════════════════════════════════════════════════════

def get_macro_trends(symbol):
    trends = {"h4_trend": "SIDEWAY", "d1_trend": "SIDEWAY", "atr_pct_h4": 0.0}
    for tf, key in [(mt5.TIMEFRAME_H4, "h4_trend"), (mt5.TIMEFRAME_D1, "d1_trend")]:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, 50)
        if rates is None or len(rates) < 50:
            continue
        df   = _calc_atr_chandelier(pd.DataFrame(rates))
        curr = df['close'].iloc[-1]
        trends[key] = "UPTREND 🟢" if curr > df['long_stop'].iloc[-1] else "DOWNTREND 🔴"
        if tf == mt5.TIMEFRAME_H4 and curr > 0:
            trends["atr_pct_h4"] = round(df['atr'].iloc[-1] / curr * 100, 3)
    return trends


# ══════════════════════════════════════════════════════════════════
#  SCOUT — momentum pre-filter
#  [UPGRADE #7] Score modifier (+0/+1/+2) instead of hard gate
# ══════════════════════════════════════════════════════════════════

def get_scout_score(symbol, order_type: str, timeframe=mt5.TIMEFRAME_M15) -> dict:
    """
    SCOUT: MACD crossover + EMA alignment.
    Returns score modifier 0-2 pts that ANALYST adds to its total.
    """
    result = {"score": 0, "macd_signal": "NEUTRAL", "ema_aligned": False,
              "reason": "Insufficient data"}

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)
    if rates is None or len(rates) < 60:
        return result

    close = pd.DataFrame(rates)['close']
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()

    bullish_cross = (macd.iloc[-2] < sig.iloc[-2]) and (macd.iloc[-1] >= sig.iloc[-1])
    bearish_cross = (macd.iloc[-2] > sig.iloc[-2]) and (macd.iloc[-1] <= sig.iloc[-1])

    if bullish_cross or macd.iloc[-1] > sig.iloc[-1]:
        macd_sig = "BULLISH"
    elif bearish_cross or macd.iloc[-1] < sig.iloc[-1]:
        macd_sig = "BEARISH"
    else:
        macd_sig = "NEUTRAL"

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    order = order_type.upper()

    ema_aligned = (ema20 > ema50) if order == "BUY" else (ema20 < ema50)
    macd_match  = (macd_sig == "BULLISH") if order == "BUY" else (macd_sig == "BEARISH")
    score = int(macd_match) + int(ema_aligned)

    return {"score": score, "macd_signal": macd_sig, "ema_aligned": ema_aligned,
            "reason": f"MACD={macd_sig} | EMA20{'>' if ema20>ema50 else '<'}EMA50 | SCOUT+{score}pts"}


# ══════════════════════════════════════════════════════════════════
#  BREAKOUT SCORE — consolidation breakout bonus (+0/+1/+2)
#  Concept from: the-trading-dev-kit breakout playbook
# ══════════════════════════════════════════════════════════════════

def get_breakout_score(symbol, order_type: str, timeframe=mt5.TIMEFRAME_M5,
                       consolidation_bars: int = 10,
                       vol_multiplier: float = 1.5) -> dict:
    """
    Breakout detection: N-bar consolidation → close outside range → volume spike.
    +2 pts: breakout + volume confirmed
    +1 pt : breakout only (volume weak / unavailable)
    +0 pts: no breakout
    """
    result = {"score": 0, "breakout": False, "reason": "No breakout"}

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, consolidation_bars + 5)
    if rates is None or len(rates) < consolidation_bars + 2:
        return result

    df    = pd.DataFrame(rates)
    order = order_type.upper()

    consol       = df.iloc[-(consolidation_bars + 1):-1]
    last         = df.iloc[-1]
    consol_high  = consol['high'].max()
    consol_low   = consol['low'].min()
    if consol_high <= consol_low:
        return result

    breakout_up   = (order == "BUY")  and (float(last['close']) > consol_high)
    breakout_down = (order == "SELL") and (float(last['close']) < consol_low)
    if not (breakout_up or breakout_down):
        result["reason"] = f"Price inside {consol_low:.2f}–{consol_high:.2f}"
        return result

    avg_vol  = float(consol['tick_volume'].mean()) if 'tick_volume' in consol.columns else 0
    last_vol = float(last.get('tick_volume', 0))
    vol_ok   = (avg_vol > 0) and (last_vol >= avg_vol * vol_multiplier)

    direction = "UP" if breakout_up else "DOWN"
    score     = 2 if vol_ok else 1
    result.update({
        "score": score, "breakout": True,
        "reason": (f"Breakout {direction} of {consol_low:.2f}–{consol_high:.2f} | "
                   f"vol {'confirmed ✓' if vol_ok else 'weak'} | +{score}pts")
    })
    return result


# ══════════════════════════════════════════════════════════════════
#  PULLBACK DEPTH SCORE — swing retracement filter (+0/+1)
#  Concept from: the-trading-dev-kit pullback playbook
#  Valid pullback: 30–65% retracement of prior swing
# ══════════════════════════════════════════════════════════════════

def get_pullback_depth_score(symbol, order_type: str, timeframe=mt5.TIMEFRAME_M5,
                              lookback: int = 25) -> dict:
    """
    +1 pt : pullback depth 30–65% of prior swing (sweet spot)
    +0 pts: too shallow (<30% = aggressive) or too deep (>65% = possible reversal)
    """
    result = {"score": 0, "depth_pct": None, "reason": "Insufficient data"}

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, lookback)
    if rates is None or len(rates) < lookback:
        return result

    df    = pd.DataFrame(rates)
    order = order_type.upper()

    highs  = df['high'].values
    lows   = df['low'].values
    closes = df['close'].values

    if order == "BUY":
        swing_high  = highs[:-3].max()
        swing_low   = lows[-5:].min()
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            result["reason"] = "Swing range = 0"
            return result
        depth = (swing_high - closes[-1]) / swing_range
    else:
        swing_low   = lows[:-3].min()
        swing_high  = highs[-5:].max()
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            result["reason"] = "Swing range = 0"
            return result
        depth = (closes[-1] - swing_low) / swing_range

    depth_pct = round(depth * 100, 1)

    if 0.30 <= depth <= 0.65:
        result.update({"score": 1, "depth_pct": depth_pct,
                       "reason": f"Pullback depth {depth_pct}% (sweet spot 30–65%) | +1pt"})
    elif depth < 0.30:
        result.update({"score": 0, "depth_pct": depth_pct,
                       "reason": f"Pullback too shallow {depth_pct}% (<30%)"})
    else:
        result.update({"score": 0, "depth_pct": depth_pct,
                       "reason": f"Pullback too deep {depth_pct}% (>65%) — possible reversal"})
    return result


# Backwards-compat alias
def get_trend_confirmation(symbol, order_type: str, timeframe=mt5.TIMEFRAME_M15) -> dict:
    s = get_scout_score(symbol, order_type, timeframe)
    return {"confirmed": s["score"] == 2, "macd_signal": s["macd_signal"],
            "ema_aligned": s["ema_aligned"], "reason": s["reason"]}
