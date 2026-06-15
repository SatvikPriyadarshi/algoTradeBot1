"""Run a handful of preset configs and print WR / trades per day."""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester

PRESETS = [
    {
        "name": "sweep_h1_adx18",
        "MTF_ENABLE_FVG": "0",
        "MTF_ENABLE_SD": "0",
        "MTF_REQUIRE_H1_BIAS": "1",
        "MTF_REQUIRE_DAILY_BIAS": "0",
        "MTF_MIN_HTF_ADX": "18",
        "MTF_MIN_BODY_ATR": "0.22",
        "MTF_LTF_RSI_LONG_MAX": "46",
        "MTF_LTF_RSI_SHORT_MIN": "54",
        "MTF_VOLUME_SPIKE": "1.15",
        "MTF_REQUIRE_VOLUME": "1",
        "MTF_REQUIRE_REJECTION": "1",
        "MTF_SHORT_BODY_MULT": "1.2",
    },
    {
        "name": "sweep_sd_h1_adx18",
        "MTF_ENABLE_FVG": "0",
        "MTF_ENABLE_SD": "1",
        "MTF_REQUIRE_H1_BIAS": "1",
        "MTF_REQUIRE_DAILY_BIAS": "0",
        "MTF_MIN_HTF_ADX": "18",
        "MTF_MIN_BODY_ATR": "0.22",
        "MTF_LTF_RSI_LONG_MAX": "46",
        "MTF_LTF_RSI_SHORT_MIN": "54",
        "MTF_VOLUME_SPIKE": "1.15",
        "MTF_SD_REQUIRE_PULLBACK": "1",
    },
    {
        "name": "sweep_h1_adx20_strict",
        "MTF_ENABLE_FVG": "0",
        "MTF_ENABLE_SD": "0",
        "MTF_REQUIRE_H1_BIAS": "1",
        "MTF_REQUIRE_DAILY_BIAS": "1",
        "MTF_MIN_HTF_ADX": "20",
        "MTF_MIN_BODY_ATR": "0.25",
        "MTF_LTF_RSI_LONG_MAX": "45",
        "MTF_LTF_RSI_SHORT_MIN": "55",
        "MTF_VOLUME_SPIKE": "1.2",
    },
]

DATASETS = [
    ("EURUSD", "data/EURUSD_M5.csv"),
    ("GBPUSD", "data/GBPUSD_M5.csv"),
    ("XAUUSD", "data/XAUUSD_M5.csv"),
]


def run_preset(name: str, cfg: dict) -> None:
    base = {
        "STRATEGY": "mtf_trend_sweep",
        "RR_RATIO": "3.0",
        "MAX_TRADES_PER_DAY": "0",
        "ENABLE_SESSION_FILTER": "1",
        "SESSION_START_UTC": "7",
        "SESSION_END_UTC": "20",
    }
    for k, v in {**base, **cfg}.items():
        os.environ[k] = str(v)

    total_trades = 0
    total_wins = 0
    total_net = 0.0
    max_days = 0
    print(f"\n--- {name} ---")
    for sym, path in DATASETS:
        if not os.path.exists(path):
            continue
        bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=sym)
        trades, _ = bt.run(path)
        if trades.empty:
            print(f"  {sym}: 0 trades")
            continue
        days = trades["entry_time"].dt.date.nunique()
        wr = (trades["net_pnl"] > 0).mean() * 100
        net = float(trades["net_pnl"].sum())
        total_trades += len(trades)
        total_wins += (trades["net_pnl"] > 0).sum()
        total_net += net
        max_days = max(max_days, days)
        print(f"  {sym}: n={len(trades):3d} WR={wr:5.1f}% tpd={len(trades)/days:.2f} net={net:+.0f}")

    if total_trades:
        wr_all = total_wins / total_trades * 100
        tpd = total_trades / max_days if max_days else 0
        print(f"  PORTFOLIO: n={total_trades} WR={wr_all:.1f}% tpd={tpd:.2f} net={total_net:+.0f}")


if __name__ == "__main__":
    for p in PRESETS:
        run_preset(p.pop("name"), p)
