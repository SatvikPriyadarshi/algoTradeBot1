import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
from backtester import StrategyBacktester


def analyze(data: str, symbol: str):
    bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=symbol)
    trades, _ = bt.run(data)
    if trades.empty:
        print(f"No trades for {symbol}")
        return
    trades["setup"] = trades["reason"].str.split("|").str[0].str.strip()
    print(f"\n=== {symbol} ===")
    for s, g in trades.groupby("setup"):
        wr = (g["net_pnl"] > 0).mean() * 100
        print(f"  {s:25s} n={len(g):3d} WR={wr:5.1f}% net={g['net_pnl'].sum():+.0f}")
    days = trades["entry_time"].dt.date.nunique()
    wr_all = (trades["net_pnl"] > 0).mean() * 100
    print(f"  TOTAL n={len(trades)} WR={wr_all:.1f}% days={days} avg/day={len(trades)/days:.2f}")


if __name__ == "__main__":
    analyze("data/EURUSD_M5.csv", "EURUSD")
