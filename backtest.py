#!/usr/bin/env python3
"""PC Trading — Backtest (Approach A: bar-by-bar simulation)."""
import argparse
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

from bot_config import SYMBOLS_CONFIG, ACCOUNT_ID, PWD, SRV

TF_MAP = {
    "M5": mt5.TIMEFRAME_M5,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
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

from advanced_indicators import _calc_atr_chandelier


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

from advanced_indicators import calculate_rsi, _find_smc_order_block, score_zone_proximity


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
    h1_slice: pd.DataFrame,
    direction: str,
    rsi_threshold: float,
) -> dict:
    """
    Mock ANALYST: compute entry score 0-12 on a pre-fetched M5 slice.

    Scoring (same as production):
      Supertrend aligned  : +3
      RSI at threshold    : +3
      SMC zone proximity  : 0 / +2 / +4
      SCOUT (MACD + EMA)  : 0 / +1 / +2

    Returns {"score": int, "rsi": float}.
    """
    score = 0

    # Supertrend
    calc = _calc_atr_chandelier(m5_slice.copy())
    last = calc.iloc[-1]
    if direction == "BUY"  and last["close"] > last["long_stop"]:
        score += 3
    elif direction == "SELL" and last["close"] < last["short_stop"]:
        score += 3

    # RSI
    rsi = calculate_rsi(m5_slice["close"].tolist())
    if direction == "BUY"  and rsi <= rsi_threshold:
        score += 3
    elif direction == "SELL" and rsi >= rsi_threshold:
        score += 3

    # SMC Order Block
    ob       = _find_smc_order_block(calc)
    score   += score_zone_proximity(last["close"], ob, direction)

    # SCOUT
    score += _compute_scout(h1_slice, direction)

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

_RSI_BUY  = {1: 30, 2: 33, 3: 35, 4: 37, 5: 40}
_RSI_SELL = {1: 70, 2: 67, 3: 65, 4: 63, 5: 60}
_FIXED_SPREAD = {"BTCUSDm": 500, "XAUUSDm": 50}
DIRECTOR_REFRESH_BARS = 48
WARMUP_BARS = 100


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
        h1_df        = data["H1"]
        h4_df        = data["H4"]
        d1_df        = data["D1"]
        point        = sym_info["point"]
        tick_value   = sym_info["tick_value"]
        lot          = cfg["lot"]
        sl_distance  = cfg["sl_pts"] * point
        tp_distance  = cfg["tp_pts"] * point
        fixed_spread = _FIXED_SPREAD.get(symbol, 100)
        max_spread   = cfg["max_spread_override"]

        rsi_buy   = _RSI_BUY[args.risk]  + cfg["rsi_buy_buffer"]
        rsi_sell  = _RSI_SELL[args.risk] - cfg["rsi_sell_buffer"]
        threshold = (args.risk + 3) + cfg["analyst_score_offset"]

        director_state = {"allowed_direction": "BOTH", "h4_trend": "N/A",
                          "last_refresh_bar": -DIRECTOR_REFRESH_BARS}
        last_sl_bar: dict = {}
        open_positions: list = []

        print(f"[INFO] Simulating {len(m5_df)} M5 bars for {symbol} "
              f"(risk {args.risk}, threshold {threshold}) ...")

        for i in range(WARMUP_BARS, len(m5_df)):
            bar_time = m5_df.iloc[i]["time"]

            # 1. Book closed positions
            still_open = []
            for pos in open_positions:
                if pos["exit_bar"] <= i:
                    if pos["result"] == "SL":
                        last_sl_bar[symbol] = pos["exit_bar"]
                    all_trades.append(pos)
                else:
                    still_open.append(pos)
            open_positions = still_open

            # 2. DIRECTOR refresh every 48 bars
            if i - director_state["last_refresh_bar"] >= DIRECTOR_REFRESH_BARS:
                h4_slice = h4_df[h4_df["time"] <= bar_time].tail(50)
                d1_slice = d1_df[d1_df["time"] <= bar_time].tail(50)
                if len(h4_slice) >= 22 and len(d1_slice) >= 22:
                    director_state.update(compute_director(h4_slice, d1_slice))
                director_state["last_refresh_bar"] = i

            # 3. ANALYST — try BUY then SELL
            m5_slice = m5_df.iloc[max(0, i - 99): i + 1]
            h1_slice = h1_df[h1_df["time"] <= bar_time].tail(100)

            for direction in ("BUY", "SELL"):
                rsi_thresh = rsi_buy if direction == "BUY" else rsi_sell
                analyst = compute_analyst_score(m5_slice, h1_slice, direction, rsi_thresh)

                if analyst["score"] < threshold:
                    continue

                # 4. GUARDIAN
                allowed, _ = check_guardian(
                    symbol=symbol, direction=direction,
                    allowed_direction=director_state["allowed_direction"],
                    last_sl_bar=last_sl_bar, current_bar_idx=i,
                    open_positions=open_positions,
                    fixed_spread=fixed_spread, max_spread=max_spread,
                )
                if not allowed:
                    continue

                # 5. Open virtual position
                entry_price = float(m5_df.iloc[i]["close"])
                if direction == "BUY":
                    sl_price = entry_price - sl_distance
                    tp_price = entry_price + tp_distance
                else:
                    sl_price = entry_price + sl_distance
                    tp_price = entry_price - tp_distance

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
                break  # one entry per bar per symbol

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

import csv
import statistics
from datetime import date


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
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
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
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
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
