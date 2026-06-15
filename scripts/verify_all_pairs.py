"""Verify OptMeanRev on all major pairs (M15)."""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester

PAIRS = ["GBPUSD", "AUDUSD", "EURUSD", "USDCAD", "USDCHF"]


def run_symbol(sym: str) -> dict:
    m15 = f"data/{sym}_M15.csv"
    m5 = f"data/{sym}_M5.csv"
    path = m15 if os.path.exists(m15) else m5
    if not os.path.exists(path):
        return {"symbol": sym, "ok": False, "error": "no data"}

    bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=sym)
    trades, _ = bt.run(path)
    if trades.empty:
        return {"symbol": sym, "ok": True, "trades": 0, "wr": 0.0, "tpd": 0.0, "net": 0.0}

    days = trades["entry_time"].dt.date.nunique()
    wr = (trades["net_pnl"] > 0).mean() * 100
    wins = (trades["net_pnl"] > 0).sum()
    losses = len(trades) - wins
    gp = trades.loc[trades["net_pnl"] > 0, "net_pnl"].sum()
    gl = abs(trades.loc[trades["net_pnl"] <= 0, "net_pnl"].sum())
    pf = float(gp / gl) if gl > 0 else 99.0
    return {
        "symbol": sym,
        "ok": True,
        "trades": len(trades),
        "wr": wr,
        "tpd": len(trades) / days if days else 0.0,
        "net": float(trades["net_pnl"].sum()),
        "pf": pf,
        "days": days,
    }


def main():
    print(f"{'Symbol':<8} {'Trades':>6} {'WR%':>6} {'T/Day':>6} {'PF':>5} {'Net $':>8}")
    print("-" * 48)
    total_trades = total_wins = 0
    total_net = 0.0
    max_days = 0
    for sym in PAIRS:
        r = run_symbol(sym)
        if not r.get("ok"):
            print(f"{sym:<8}  SKIP  {r.get('error', '')}")
            continue
        print(
            f"{r['symbol']:<8} {r['trades']:6d} {r['wr']:6.1f} {r['tpd']:6.2f} "
            f"{r['pf']:5.2f} {r['net']:+8.0f}"
        )
        total_trades += r["trades"]
        total_wins += int(r["trades"] * r["wr"] / 100)
        total_net += r["net"]
        max_days = max(max_days, r.get("days", 0))

    if total_trades:
        wr_all = total_wins / total_trades * 100
        tpd = total_trades / max_days if max_days else 0
        print("-" * 48)
        print(f"{'TOTAL':<8} {total_trades:6d} {wr_all:6.1f} {tpd:6.2f}       {total_net:+8.0f}")


if __name__ == "__main__":
    main()
