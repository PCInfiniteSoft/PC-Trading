"""Aggregate per-scenario backtest CSV exports into a composite comparison table.

Usage:
  python analyze_trend_sell.py baseline.csv st1.csv st2.csv st3.csv

Each CSV is a backtest trade log (from backtest.py --export). Prints one row per
(scenario, symbol) with the composite metrics from the design spec.
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


def _max_dd(rows):
    eq = peak = mdd = 0.0
    for r in sorted(rows, key=lambda x: x["exit_time"]):
        eq += float(r["net_profit"])
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, min((peak - eq) / peak * 100, 100.0))
    return round(mdd, 1)


def _metrics(rows):
    profits = [float(r["net_profit"]) for r in rows]
    wins = [p for p in profits if p > 0]
    sells = [r for r in rows if r["direction"] == "SELL"]
    sell_down = [r for r in sells if r.get("d1_trend") == "DOWNTREND"]
    avg_r = (statistics.mean([p for p in profits]) if profits else 0.0)
    return {
        "trades": len(rows),
        "wr": round(len(wins) / len(profits) * 100, 1) if profits else 0.0,
        "pnl": round(sum(profits), 2),
        "max_dd": _max_dd(rows),
        "sharpe": _sharpe(rows),
        "sells": len(sells),
        "sell_down": len(sell_down),
        "avg_pnl": round(avg_r, 3),
    }


def main(paths):
    hdr = f"{'scenario':<10}{'symbol':<10}{'trades':>7}{'WR%':>7}{'PnL':>10}" \
          f"{'MaxDD%':>8}{'Sharpe':>8}{'#SELL':>7}{'#SELLdn':>9}{'avgPnL':>9}"
    print(hdr)
    print("-" * len(hdr))
    for path in paths:
        scenario = Path(path).stem
        rows = _load(path)
        symbols = sorted({r["symbol"] for r in rows})
        for sym in symbols:
            srows = [r for r in rows if r["symbol"] == sym]
            m = _metrics(srows)
            print(f"{scenario:<10}{sym:<10}{m['trades']:>7}{m['wr']:>7}"
                  f"{m['pnl']:>10}{m['max_dd']:>8}{m['sharpe']:>8}"
                  f"{m['sells']:>7}{m['sell_down']:>9}{m['avg_pnl']:>9}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
