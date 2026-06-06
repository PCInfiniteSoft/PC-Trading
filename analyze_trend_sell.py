"""Aggregate per-scenario backtest CSV exports into a composite comparison table.

Usage:
  python analyze_trend_sell.py baseline.csv st1.csv st2.csv st3.csv

Each CSV is a backtest trade log (from backtest.py --export). Prints one row per
(scenario, symbol) with the composite metrics from the design spec.

INTERPRETATION NOTES:
- The st1/st2/st3 rows include BOTH the trend-sell entries AND the baseline
  mean-reversion entries that occur on non-DOWNTREND bars (the trend-sell path only
  fires on a confirmed D1 DOWNTREND; other bars fall through to mean-reversion). To
  isolate the trigger's own trades, filter the source CSV by entry_reason=="trend_sell".
- st2 (EMA bearish-alignment) is a STATE trigger, not a one-shot event, so it tends to
  fire on many more bars during a sustained downtrend than st1/st3 — compare the
  #trades column before reading WR/MaxDD across scenarios as apples-to-apples.
- Backtest uses a MOCK analyst (not live GPT); treat numbers as relative, not absolute.
"""
import csv
import statistics
import sys
from pathlib import Path


def _load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _sharpe(rows):
    by_day = {}
    for r in rows:
        day = r["exit_time"][:10]
        by_day[day] = by_day.get(day, 0.0) + float(r["net_profit"])
    daily = list(by_day.values())
    if len(daily) < 2:
        return 0.0
    sd = statistics.stdev(daily)
    return round(statistics.mean(daily) / sd * (252 ** 0.5), 2) if sd else 0.0


def _max_dd(rows, base):
    """Max drawdown % of the equity curve = base capital + cumulative P&L.

    `base` MUST be the starting account equity (not 0), otherwise an early dip
    below a tiny from-zero peak saturates the figure to ~100% for almost any curve.
    """
    eq = peak = float(base)
    mdd = 0.0
    for r in sorted(rows, key=lambda x: x["exit_time"]):
        eq += float(r["net_profit"])
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, min((peak - eq) / peak * 100, 100.0))
    return round(mdd, 1)


def _metrics(rows, base):
    profits = [float(r["net_profit"]) for r in rows]
    wins = [p for p in profits if p > 0]
    sells = [r for r in rows if r["direction"] == "SELL"]
    sell_down = [r for r in sells if r.get("d1_trend") == "DOWNTREND"]
    avg_r = (statistics.mean([p for p in profits]) if profits else 0.0)
    return {
        "trades": len(rows),
        "wr": round(len(wins) / len(profits) * 100, 1) if profits else 0.0,
        "pnl": round(sum(profits), 2),
        "max_dd": _max_dd(rows, base),
        "sharpe": _sharpe(rows),
        "sells": len(sells),
        "sell_down": len(sell_down),
        "avg_pnl": round(avg_r, 3),
    }


def main(paths, capital=300.0):
    hdr = f"{'scenario':<10}{'symbol':<10}{'trades':>7}{'WR%':>7}{'PnL':>10}" \
          f"{'MaxDD%':>8}{'Sharpe':>8}{'#SELL':>7}{'#SELLdn':>9}{'avgPnL':>9}"
    print(f"(MaxDD% computed vs starting equity = capital/num_symbols; capital={capital})")
    print(hdr)
    print("-" * len(hdr))
    for path in paths:
        scenario = Path(path).stem
        rows = _load(path)
        symbols = sorted({r["symbol"] for r in rows})
        # backtest.py splits capital equally across the symbols in the run
        base = capital / len(symbols) if symbols else capital
        for sym in symbols:
            srows = [r for r in rows if r["symbol"] == sym]
            m = _metrics(srows, base)
            print(f"{scenario:<10}{sym:<10}{m['trades']:>7}{m['wr']:>7}"
                  f"{m['pnl']:>10}{m['max_dd']:>8}{m['sharpe']:>8}"
                  f"{m['sells']:>7}{m['sell_down']:>9}{m['avg_pnl']:>9}")


if __name__ == "__main__":
    args = sys.argv[1:]
    capital = 300.0
    if "--capital" in args:
        idx = args.index("--capital")
        capital = float(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    if not args:
        print(__doc__)
        sys.exit(1)
    main(args, capital)
