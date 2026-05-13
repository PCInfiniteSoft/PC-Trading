#!/usr/bin/env python3
"""PC Trading — Backtest (Approach A: bar-by-bar simulation)."""
import argparse
import csv
import statistics
import sys
from datetime import date, datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

from advanced_indicators import (
    _calc_atr_chandelier,
    _find_smc_order_block,
    calculate_rsi,
    score_zone_proximity,
)
from bot_config import ACCOUNT_ID, PWD, SRV, SYMBOLS_CONFIG

TF_MAP = {
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}


def load_data(symbol: str, months: int) -> dict:
    """Fetch OHLCV candles for all needed timeframes. Returns {tf_name: DataFrame}."""
    end   = datetime.now()
    start = end - timedelta(days=months * 31)
    result = {}
    for tf_name, tf_const in TF_MAP.items():
        rates = mt5.copy_rates_range(symbol, tf_const, start, end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No data for {symbol} {tf_name} — is MT5 connected and symbol active?")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        result[tf_name] = df[["time", "open", "high", "low", "close", "tick_volume"]].reset_index(drop=True)
    return result


def load_symbol_info(symbol: str) -> dict:
    """Return point size and tick value for a symbol."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol info not found for {symbol}")
    return {"point": info.point, "tick_value": info.trade_tick_value}


# ── MockDirector ──────────────────────────────────────────────────

def _get_trend(df: pd.DataFrame) -> str:
    """
    Determine trend from Chandelier Exit direction.
    close > long_stop  -> UPTREND
    close < short_stop -> DOWNTREND
    otherwise          -> SIDEWAY
    Requires at least 22 rows (Chandelier default period).
    """
    calc = _calc_atr_chandelier(df.copy())
    last = calc.iloc[-1]
    if last["close"] > last["long_stop"]:
        return "UPTREND"
    if last["close"] < last["short_stop"]:
        return "DOWNTREND"
    return "SIDEWAY"


def compute_director(h4_slice: pd.DataFrame, d1_slice: pd.DataFrame) -> dict:
    """
    Mock DIRECTOR: compute allowed_direction from H4 and D1 Chandelier trends.
    Returns {"allowed_direction": str, "h4_trend": str, "d1_trend": str}.
    """
    h4_trend = _get_trend(h4_slice) if len(h4_slice) >= 22 else "SIDEWAY"
    d1_trend = _get_trend(d1_slice) if len(d1_slice) >= 22 else "SIDEWAY"

    if h4_trend == "UPTREND" and d1_trend == "UPTREND":
        direction = "BUY_ONLY"
    elif h4_trend == "DOWNTREND" and d1_trend == "DOWNTREND":
        direction = "SELL_ONLY"
    else:
        direction = "BOTH"

    return {"allowed_direction": direction, "h4_trend": h4_trend, "d1_trend": d1_trend}


# ── MockAnalyst ───────────────────────────────────────────────────

def _compute_scout(h1_slice: pd.DataFrame, direction: str) -> int:
    """
    Replicate get_scout_score() logic on a pre-fetched H1 slice.
    MACD crossover/alignment (+1) + EMA20 vs EMA50 alignment (+1) = 0-2 pts.
    Requires at least 60 rows; returns 0 if insufficient data.
    """
    if len(h1_slice) < 60:
        return 0

    closes = h1_slice["close"]
    ema12  = closes.ewm(span=12, adjust=False).mean()
    ema26  = closes.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    bullish_cross = (macd.iloc[-2] < signal.iloc[-2]) and (macd.iloc[-1] >= signal.iloc[-1])
    bearish_cross = (macd.iloc[-2] > signal.iloc[-2]) and (macd.iloc[-1] <= signal.iloc[-1])

    if bullish_cross or macd.iloc[-1] > signal.iloc[-1]:
        macd_sig = "BULLISH"
    elif bearish_cross or macd.iloc[-1] < signal.iloc[-1]:
        macd_sig = "BEARISH"
    else:
        macd_sig = "NEUTRAL"

    ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = closes.ewm(span=50, adjust=False).mean().iloc[-1]

    ema_aligned = (ema20 > ema50) if direction == "BUY" else (ema20 < ema50)
    macd_match  = (macd_sig == "BULLISH") if direction == "BUY" else (macd_sig == "BEARISH")

    return int(ema_aligned) + int(macd_match)


def compute_analyst_score(
    m5_slice: pd.DataFrame,
    m15_slice: pd.DataFrame,
    h1_slice: pd.DataFrame,
    direction: str,
) -> dict:
    """
    Mock ANALYST: mirrors production ai_analysis scoring exactly.

    Scoring (matches ai_engine.py prompt):
      H1  Supertrend aligned : +4  (production primary criterion)
      SMC zone proximity      : 0 / +2 / +4  (from M15 data)
      RSI < 40 (BUY) / > 60  : +2  (from M5 data, hardcoded thresholds)
      SCOUT (MACD + EMA M15)  : 0 / +1 / +2

    Returns {"score": int, "rsi": float}.
    """
    score = 0

    # H1 Supertrend — +4 pts (production primary criterion)
    calc_h1 = _calc_atr_chandelier(h1_slice.copy())
    last_h1 = calc_h1.iloc[-1]
    if direction == "BUY"  and last_h1["close"] > last_h1["long_stop"]:
        score += 4
    elif direction == "SELL" and last_h1["close"] < last_h1["short_stop"]:
        score += 4

    # RSI from M5 — +2 pts, hardcoded 40/60 thresholds (matches prompt)
    rsi = calculate_rsi(m5_slice["close"].tolist())
    if direction == "BUY"  and rsi <= 40:
        score += 2
    elif direction == "SELL" and rsi >= 60:
        score += 2

    # SMC Order Block from M15 (matches production get_3_indicators default M15)
    calc_m15 = _calc_atr_chandelier(m15_slice.copy())
    ob        = _find_smc_order_block(calc_m15)
    price     = float(m5_slice.iloc[-1]["close"])
    score    += score_zone_proximity(price, ob, direction)

    # SCOUT from M15 (matches production get_scout_score default M15)
    score += _compute_scout(m15_slice, direction)

    return {"score": min(score, 12), "rsi": rsi}


# ── MockGuardian ──────────────────────────────────────────────────

def check_guardian(
    symbol: str,
    direction: str,
    allowed_direction: str,
    last_sl_bar: dict,
    current_bar_idx: int,
    open_positions: list,
    fixed_spread: int,
    max_spread: int,
    max_layers: int = 3,
) -> tuple:
    """
    Mock GUARDIAN: apply 4 sequential gates before allowing an entry.
    Returns (allowed: bool, reason: str).
    """
    # Gate 1: cooldown — block entry if SL hit at current or previous bar
    if current_bar_idx - last_sl_bar.get(symbol, -9999) <= 1:
        return False, "COOLDOWN"

    # Gate 2: direction vs DIRECTOR
    if allowed_direction == "BUY_ONLY"  and direction == "SELL":
        return False, "DIRECTION"
    if allowed_direction == "SELL_ONLY" and direction == "BUY":
        return False, "DIRECTION"

    # Gate 3: spread
    if fixed_spread > max_spread:
        return False, "SPREAD"

    # Gate 4: max layers per symbol
    symbol_layers = sum(1 for p in open_positions if p["symbol"] == symbol)
    if symbol_layers >= max_layers:
        return False, "MAX_LAYERS"

    return True, "OK"


# ── Dynamic SL/TP ────────────────────────────────────────────────

def compute_dynamic_sl_tp(
    entry_price: float,
    m5_slice: pd.DataFrame,
    direction: str,
    rr: float = 2.0,
) -> tuple | None:
    """
    Chandelier Exit-based dynamic SL and TP.

    SL = M5 Chandelier long_stop (BUY) or short_stop (SELL).
    TP = entry ± sl_distance × rr  (default 2:1 R:R).

    Returns (sl_price, tp_price, sl_distance) or None if SL is invalid
    (price is already on the wrong side of the Chandelier stop).
    """
    calc = _calc_atr_chandelier(m5_slice.copy())
    last = calc.iloc[-1]

    if direction == "BUY":
        sl_price    = float(last["long_stop"])
        sl_distance = entry_price - sl_price
        if sl_distance <= 0 or pd.isna(sl_price):
            return None
        tp_price = entry_price + sl_distance * rr

    else:  # SELL
        sl_price    = float(last["short_stop"])
        sl_distance = sl_price - entry_price
        if sl_distance <= 0 or pd.isna(sl_price):
            return None
        tp_price = entry_price - sl_distance * rr

    return sl_price, tp_price, sl_distance


# ── PositionSimulator ─────────────────────────────────────────────

def simulate_position_exit(
    m5_df: pd.DataFrame,
    entry_bar_idx: int,
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
) -> dict:
    """
    Scan forward from entry_bar_idx+1 to find SL or TP hit.
    If both hit in the same candle, SL wins (conservative).
    If price never hits either, force-close at last bar's close.

    Returns {"exit_bar": int, "exit_price": float, "result": str, "exit_time": str}.
    """
    for i in range(entry_bar_idx + 1, len(m5_df)):
        row = m5_df.iloc[i]
        if direction == "BUY":
            tp_hit = row["high"] >= tp_price
            sl_hit = row["low"]  <= sl_price
        else:
            tp_hit = row["low"]  <= tp_price
            sl_hit = row["high"] >= sl_price

        if sl_hit and tp_hit:
            return {"exit_bar": i, "exit_price": sl_price,
                    "result": "SL", "exit_time": str(row["time"])}
        if sl_hit:
            return {"exit_bar": i, "exit_price": sl_price,
                    "result": "SL", "exit_time": str(row["time"])}
        if tp_hit:
            return {"exit_bar": i, "exit_price": tp_price,
                    "result": "TP", "exit_time": str(row["time"])}

    last = m5_df.iloc[-1]
    return {"exit_bar": len(m5_df) - 1, "exit_price": float(last["close"]),
            "result": "OPEN", "exit_time": str(last["time"])}


def compute_net_profit(
    direction: str,
    entry_price: float,
    exit_price: float,
    lot: float,
    tick_value: float,
    point: float,
) -> float:
    """
    Net profit in account currency.
    Formula: (price_diff_in_points) x tick_value x lot
    tick_value is per 1 point per 1 lot (from mt5.symbol_info.trade_tick_value).
    """
    price_diff = exit_price - entry_price
    if direction == "SELL":
        price_diff = -price_diff
    points_gained = price_diff / point
    return round(points_gained * tick_value * lot, 2)


def parse_args():
    p = argparse.ArgumentParser(description="PC Trading backtest")
    p.add_argument("--months",  type=int, default=3, metavar="N",
                   help="Lookback period in months (default: 3)")
    p.add_argument("--symbols", nargs="+", default=list(SYMBOLS_CONFIG.keys()),
                   metavar="SYM", help="Symbols to test")
    p.add_argument("--risk",    type=int, default=3, choices=range(1, 6), metavar="1-5",
                   help="Risk level 1-5 (default: 3)")
    p.add_argument("--export",  type=str, default=None, metavar="PATH",
                   help="Export trade log to CSV")
    p.add_argument("--capital", type=float, default=1000.0, metavar="USD",
                   help="Starting capital in USD, split equally across symbols (default: 1000)")
    p.add_argument("--lot",     type=float, default=None, metavar="SIZE",
                   help="Override lot size for all symbols (e.g. 0.05)")
    return p.parse_args()


def init_mt5() -> bool:
    if not mt5.initialize(login=ACCOUNT_ID, password=PWD, server=SRV):
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}")
        return False
    return True


def main():
    args = parse_args()
    if not init_mt5():
        sys.exit(1)
    try:
        trades = run_backtest(args)
        print_report(trades, args)
        if args.export:
            export_csv(trades, args.export)
    finally:
        mt5.shutdown()


# ── Integration constants ─────────────────────────────────────────

# Matches ai_engine.py base_trigger table exactly
_BASE_TRIGGER = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4}

_FIXED_SPREAD = {"BTCUSDm": 500, "XAUUSDm": 50}
DIRECTOR_REFRESH_BARS = 48
WARMUP_BARS = 100

# Risk management constants
_DAILY_LOSS_LIMIT    = {"BTCUSDm": -8.0, "XAUUSDm": -20.0}
_CIRCUIT_BREAKER_PCT  = 20.0  # halt if equity drops this % below running peak
_CIRCUIT_BREAKER_DAYS = 5     # calendar days to stay halted before resuming


def _is_safe_bar_time(bar_time: pd.Timestamp, symbol: str) -> bool:
    """
    Replicate trade_manager.is_safe_trading_time for bar-by-bar simulation.
    BTC: always True.  XAU: closed weekends + Monday before 08:00 Thai time.
    """
    if "BTC" in symbol.upper():
        return True
    import datetime
    thai = bar_time + pd.Timedelta(hours=7)
    wd   = thai.weekday()          # 0=Mon … 5=Sat, 6=Sun
    t    = thai.time()
    if wd == 5 and t > datetime.time(3, 30):
        return False
    if wd == 6:
        return False
    if wd == 0 and t < datetime.time(8, 0):
        return False
    return True


def run_backtest(args) -> list:
    """
    Main orchestrator: bar-by-bar simulation for each symbol.
    Returns a flat list of closed trade dicts.
    """
    all_trades = []

    for symbol in args.symbols:
        cfg          = SYMBOLS_CONFIG[symbol]
        print(f"[INFO] Loading data for {symbol} ...")
        data         = load_data(symbol, args.months)
        sym_info     = load_symbol_info(symbol)
        m5_df        = data["M5"]
        m15_df       = data["M15"]
        h1_df        = data["H1"]
        h4_df        = data["H4"]
        d1_df        = data["D1"]
        point        = sym_info["point"]
        tick_value   = sym_info["tick_value"]
        lot          = args.lot if args.lot is not None else cfg["lot"]
        fixed_spread = _FIXED_SPREAD.get(symbol, 100)
        max_spread   = cfg["max_spread_override"]

        # Mirrors ai_engine.py: base_trigger table + per-symbol offset
        threshold = max(4, _BASE_TRIGGER[args.risk] + cfg["analyst_score_offset"])

        director_state = {"allowed_direction": "BOTH", "h4_trend": "N/A",
                          "last_refresh_bar": -DIRECTOR_REFRESH_BARS}
        last_sl_bar: dict = {}
        open_positions: list = []

        # Risk management state
        lot_scale    = lot / 0.01  # scale daily limits relative to base lot=0.01
        sym_equity   = args.capital / len(args.symbols)
        sym_peak     = sym_equity
        cb_resume    = None   # date when circuit breaker lifts (None = not triggered)
        daily_pnl: dict = {}  # {date: cumulative closed P&L that day}
        daily_paused: set = set()  # calendar dates where daily limit was hit

        print(f"[INFO] Simulating {len(m5_df)} M5 bars for {symbol} "
              f"(risk {args.risk}, threshold {threshold}) ...")

        for i in range(WARMUP_BARS, len(m5_df)):
            bar_time = m5_df.iloc[i]["time"]

            # 1. Session filter — skip closed-market bars (XAU only)
            if not _is_safe_bar_time(bar_time, symbol):
                continue

            # 2. Book closed positions + update risk management state
            still_open = []
            for pos in open_positions:
                if pos["exit_bar"] <= i:
                    if pos["result"] == "SL":
                        last_sl_bar[symbol] = pos["exit_bar"]
                    all_trades.append(pos)

                    sym_equity += pos["net_profit"]
                    if sym_equity > sym_peak:
                        sym_peak = sym_equity

                    # Circuit breaker: halt if equity drops >= 20% from peak
                    if sym_peak > 0 and cb_resume is None:
                        dd_pct = (sym_peak - sym_equity) / sym_peak * 100
                        if dd_pct >= _CIRCUIT_BREAKER_PCT:
                            cb_resume = (bar_time + pd.Timedelta(days=_CIRCUIT_BREAKER_DAYS)).date()

                    # Daily loss limit
                    exit_date = pd.Timestamp(pos["exit_time"]).date()
                    daily_pnl[exit_date] = daily_pnl.get(exit_date, 0.0) + pos["net_profit"]
                    if daily_pnl[exit_date] <= _DAILY_LOSS_LIMIT.get(symbol, float("-inf")) * lot_scale:
                        daily_paused.add(exit_date)
                else:
                    still_open.append(pos)
            open_positions = still_open

            # 2b. Risk management halts — skip new entries if limit hit
            today = bar_time.date()
            if today in daily_paused:
                continue
            if cb_resume:
                if today < cb_resume:
                    continue
                # Halt period over: resume and reset peak so % is measured from here
                cb_resume = None
                sym_peak  = sym_equity

            # 3. DIRECTOR refresh every 48 bars
            if i - director_state["last_refresh_bar"] >= DIRECTOR_REFRESH_BARS:
                h4_slice = h4_df[h4_df["time"] <= bar_time].tail(50)
                d1_slice = d1_df[d1_df["time"] <= bar_time].tail(50)
                if len(h4_slice) >= 22 and len(d1_slice) >= 22:
                    director_state.update(compute_director(h4_slice, d1_slice))
                director_state["last_refresh_bar"] = i

            # 4. ANALYST — one direction per bar, chosen by RSI vs 50 (mirrors production)
            m5_slice  = m5_df.iloc[max(0, i - 99): i + 1]
            m15_slice = m15_df[m15_df["time"] <= bar_time].tail(100)
            h1_slice  = h1_df[h1_df["time"]  <= bar_time].tail(100)

            rsi_now = calculate_rsi(m5_slice["close"].tolist())
            if rsi_now <= 40:
                direction = "BUY"
            elif rsi_now >= 60:
                direction = "SELL"
            else:
                continue  # RSI in neutral zone — no trade

            # H1 Supertrend hard filter — direction must align before scoring
            h1_calc  = _calc_atr_chandelier(h1_slice.copy())
            last_h1  = h1_calc.iloc[-1]
            if direction == "BUY"  and float(last_h1["close"]) <= float(last_h1["long_stop"]):
                continue
            if direction == "SELL" and float(last_h1["close"]) >= float(last_h1["short_stop"]):
                continue

            analyst = compute_analyst_score(m5_slice, m15_slice, h1_slice, direction)

            if analyst["score"] < threshold:
                continue

            # 4b. XAU quality filters
            if "XAU" in symbol.upper():
                # Block BUY_ONLY state — H4/D1 uptrend on gold produces poor BUY entries
                if director_state["allowed_direction"] == "BUY_ONLY":
                    continue
                # Block dead-zone hours (Asian noise, low-volume)
                thai_hour = (bar_time + pd.Timedelta(hours=7)).hour
                if thai_hour in (2, 5, 6, 17, 22):
                    continue

            # 4c. BTC off-hours filter (Approach A+C)
            # Off-hours (outside 07-17 Thai) require stricter RSI + higher score
            if "BTC" in symbol.upper():
                thai_hour = (bar_time + pd.Timedelta(hours=7)).hour
                is_peak   = 7 <= thai_hour <= 17
                if not is_peak:
                    rsi_ok  = (direction == "BUY" and rsi_now <= 32) or \
                              (direction == "SELL" and rsi_now >= 68)
                    if not (rsi_ok and analyst["score"] >= 7):
                        continue

            # 5. GUARDIAN
            allowed, _ = check_guardian(
                symbol=symbol, direction=direction,
                allowed_direction=director_state["allowed_direction"],
                last_sl_bar=last_sl_bar, current_bar_idx=i,
                open_positions=open_positions,
                fixed_spread=fixed_spread, max_spread=max_spread,
            )
            if not allowed:
                continue

            # 6. Open virtual position
            entry_price = float(m5_df.iloc[i]["close"])
            sl_tp = compute_dynamic_sl_tp(entry_price, m5_slice, direction)
            if sl_tp is None:
                continue
            sl_price, tp_price, sl_distance = sl_tp

            exit_info = simulate_position_exit(
                m5_df, i, direction, entry_price, sl_price, tp_price
            )
            net_p  = compute_net_profit(
                direction, entry_price, exit_info["exit_price"], lot, tick_value, point
            )
            layers = sum(1 for p in open_positions if p["symbol"] == symbol) + 1

            open_positions.append({
                "symbol":            symbol,
                "direction":         direction,
                "entry_bar":         i,
                "entry_time":        str(bar_time),
                "entry_price":       entry_price,
                "sl_price":          sl_price,
                "tp_price":          tp_price,
                "rsi_entry":         analyst["rsi"],
                "score":             analyst["score"],
                "h4_trend":          director_state.get("h4_trend", "N/A"),
                "allowed_direction": director_state["allowed_direction"],
                "layers":            layers,
                "net_profit":        net_p,
                **exit_info,
            })

        # Force-close any still-open positions at end of data
        last_bar = m5_df.iloc[-1]
        for pos in open_positions:
            pos.setdefault("exit_bar",   len(m5_df) - 1)
            pos.setdefault("exit_price", float(last_bar["close"]))
            pos.setdefault("exit_time",  str(last_bar["time"]))
            pos.setdefault("result",     "OPEN")
            if "net_profit" not in pos:
                pos["net_profit"] = compute_net_profit(
                    pos["direction"], pos["entry_price"], pos["exit_price"],
                    lot, tick_value, point
                )
            all_trades.append(pos)

    return all_trades


# ── ReportPrinter ─────────────────────────────────────────────────

def _symbol_metrics(trades: list, symbol: str) -> dict:
    """Compute per-symbol stats from a list of trade dicts."""
    sym_trades = [t for t in trades if t["symbol"] == symbol]
    if not sym_trades:
        return None
    profits = [t["net_profit"] for t in sym_trades]
    wins    = [p for p in profits if p > 0]
    losses  = [p for p in profits if p <= 0]
    win_rate = len(wins) / len(profits) * 100 if profits else 0

    equity = peak = max_dd = 0.0
    for p in profits:
        equity += p
        if equity > peak:
            peak = equity
        dd = min((peak - equity) / peak * 100, 100.0) if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    max_consec = consec = 0
    for p in profits:
        if p <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    return {
        "symbol":     symbol,
        "total":      len(sym_trades),
        "win_rate":   win_rate,
        "net_pnl":    sum(profits),
        "avg_win":    sum(wins)   / len(wins)   if wins   else 0,
        "avg_loss":   sum(losses) / len(losses) if losses else 0,
        "max_dd":     max_dd,
        "max_consec": max_consec,
    }


def _sharpe(trades: list) -> float:
    """Approximate annualised Sharpe ratio from daily P&L."""
    if not trades:
        return 0.0
    by_day: dict = {}
    for t in trades:
        day = t["exit_time"][:10]
        by_day[day] = by_day.get(day, 0.0) + t["net_profit"]
    daily = list(by_day.values())
    if len(daily) < 2:
        return 0.0
    mean  = statistics.mean(daily)
    stdev = statistics.stdev(daily)
    if stdev == 0:
        return 0.0
    return round(mean / stdev * (252 ** 0.5), 2)


def print_report(trades: list, args) -> None:
    """Print backtest summary to stdout."""
    end_date   = date.today()
    start_date = end_date - timedelta(days=args.months * 31)
    sep = "=" * 60

    print(f"\n{sep}")
    print("  PC TRADING — BACKTEST REPORT")
    print(f"  Period : {start_date} -> {end_date}  ({args.months} months)")
    print(f"  Risk   : Level {args.risk}  |  Symbols: {', '.join(args.symbols)}")
    print(sep)

    for sym in args.symbols:
        m = _symbol_metrics(trades, sym)
        if m is None:
            print(f"\n[ {sym} ]  No trades generated.")
            continue
        print(f"\n[ {sym} ]")
        print(f"  Total Trades      : {m['total']}")
        print(f"  Win Rate          : {m['win_rate']:.1f}%")
        print(f"  Net P&L           : {m['net_pnl']:+.2f}")
        print(f"  Avg Win / Loss    : {m['avg_win']:+.2f} / {m['avg_loss']:+.2f}")
        print(f"  Max Drawdown      : {m['max_dd']:.1f}%")
        print(f"  Max Consec. Loss  : {m['max_consec']}")

    if len(args.symbols) > 1 and trades:
        total   = len(trades)
        wins    = sum(1 for t in trades if t["net_profit"] > 0)
        net_pnl = sum(t["net_profit"] for t in trades)
        sharpe  = _sharpe(trades)

        eq = peak = max_dd = 0.0
        for p in sorted(trades, key=lambda t: t["exit_time"]):
            eq += p["net_profit"]
            if eq > peak: peak = eq
            dd = min((peak - eq) / peak * 100, 100.0) if peak > 0 else 0
            if dd > max_dd: max_dd = dd

        print(f"\n[ OVERALL ]")
        print(f"  Total Trades      : {total}")
        print(f"  Net P&L           : {net_pnl:+.2f}")
        print(f"  Max Drawdown      : {max_dd:.1f}%")
        print(f"  Sharpe Ratio      : {sharpe}")
        print(f"  Win Rate          : {wins/total*100:.1f}%")

    print(f"\n{sep}\n")


def export_csv(trades: list, path: str) -> None:
    """Export trade log to CSV."""
    fields = ["symbol", "direction", "entry_time", "entry_price",
              "exit_price", "net_profit", "result", "exit_time",
              "rsi_entry", "score", "h4_trend", "allowed_direction", "layers"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)
    print(f"[INFO] Trade log exported to {path} ({len(trades)} rows)")


if __name__ == "__main__":
    main()
