"""Portfolio backtest with current .env settings across all CSV data."""

from __future__ import annotations

import glob
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester


def main():
    paths = sorted(glob.glob("data/*_M5.csv"))
    total_trades = 0
    total_wins = 0
    total_net = 0.0
    max_days = 0

    print("Symbol       Trades   WR%   tpd    Net")
    print("-" * 42)
    for path in paths:
        sym = os.path.basename(path).replace("_M5.csv", "")
        bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=sym)
        trades, _ = bt.run(path)
        if trades.empty:
            print(f"{sym:10s}      0     -     -      -")
            continue
        days = trades["entry_time"].dt.date.nunique()
        wr = (trades["net_pnl"] > 0).mean() * 100
        net = float(trades["net_pnl"].sum())
        total_trades += len(trades)
        total_wins += (trades["net_pnl"] > 0).sum()
        total_net += net
        max_days = max(max_days, days)
        print(f"{sym:10s} {len(trades):6d} {wr:6.1f} {len(trades)/days:5.2f} {net:+7.0f}")

    if total_trades:
        wr_all = total_wins / total_trades * 100
        tpd = total_trades / max_days if max_days else 0
        print("-" * 42)
        print(f"{'PORTFOLIO':10s} {total_trades:6d} {wr_all:6.1f} {tpd:5.2f} {total_net:+7.0f}")


if __name__ == "__main__":
    main()
