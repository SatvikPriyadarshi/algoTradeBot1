"""Quick strategy parameter sweep for win rate at 1:3 RR."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester


def run_once(strategy: str, env_overrides: dict, data: str, symbol: str, risk: float = 30.0):
    for k, v in env_overrides.items():
        os.environ[k] = str(v)
    os.environ["STRATEGY"] = strategy
    bt = StrategyBacktester(
        risk_per_trade_cash=risk,
        trade_capital_usd=500,
        symbol=symbol,
        strategy_name=strategy,
    )
    trades, _ = bt.run(data)
    if trades.empty:
        return {"trades": 0, "win_rate": 0.0, "net": 0.0}
    wins = (trades["net_pnl"] > 0).sum()
    return {
        "trades": len(trades),
        "win_rate": wins / len(trades) * 100,
        "net": float(trades["net_pnl"].sum()),
    }


def main():
    data = sys.argv[1] if len(sys.argv) > 1 else "data/XAUUSD_M5.csv"
    symbol = sys.argv[2] if len(sys.argv) > 2 else "XAUUSD"

    grids = []
    for htf in ("1h", "4h"):
        for swing in (3, 5, 8):
            for ltf_rsi_long in (45, 50, 55):
                for min_body in (0.15, 0.25, 0.35):
                    grids.append({
                        "MTF_HTF_RULE": htf,
                        "MTF_LTF_SWING": swing,
                        "MTF_LTF_RSI_LONG_MAX": ltf_rsi_long,
                        "MTF_LTF_RSI_SHORT_MIN": 100 - ltf_rsi_long,
                        "MTF_MIN_BODY_ATR": min_body,
                        "ENABLE_SESSION_FILTER": "1",
                        "MAX_TRADES_PER_DAY": "0",
                    })

    best = None
    for i, cfg in enumerate(grids):
        r = run_once("mtf_trend_sweep", cfg, data, symbol)
        if r["trades"] < 8:
            continue
        score = r["win_rate"]
        if best is None or score > best["win_rate"] or (
            score == best["win_rate"] and r["net"] > best["net"]
        ):
            best = {**cfg, **r}

    print("BEST MTF CONFIG (min 8 trades):")
    print(best or "No config met minimum trade count")


if __name__ == "__main__":
    main()
