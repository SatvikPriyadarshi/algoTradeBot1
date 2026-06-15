"""Compare filter presets on full portfolio."""

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

PRESETS = {
    "current": {
        "MTF_ENABLE_FVG": "0", "MTF_ENABLE_SD": "0",
        "MTF_REQUIRE_H1_BIAS": "1", "MTF_MIN_HTF_ADX": "18",
        "MTF_MIN_BODY_ATR": "0.22", "MTF_LTF_RSI_LONG_MAX": "46",
        "MTF_LTF_RSI_SHORT_MIN": "54", "MTF_VOLUME_SPIKE": "1.15",
        "MTF_SHORT_BODY_MULT": "1.2",
    },
    "adx20_body24": {
        "MTF_ENABLE_FVG": "0", "MTF_ENABLE_SD": "0",
        "MTF_REQUIRE_H1_BIAS": "1", "MTF_MIN_HTF_ADX": "20",
        "MTF_MIN_BODY_ATR": "0.24", "MTF_LTF_RSI_LONG_MAX": "45",
        "MTF_LTF_RSI_SHORT_MIN": "55", "MTF_VOLUME_SPIKE": "1.2",
        "MTF_SHORT_BODY_MULT": "1.25",
    },
    "forex_only": {
        "MTF_ENABLE_FVG": "0", "MTF_ENABLE_SD": "0",
        "MTF_REQUIRE_H1_BIAS": "1", "MTF_MIN_HTF_ADX": "18",
        "MTF_MIN_BODY_ATR": "0.22", "MTF_LTF_RSI_LONG_MAX": "46",
        "MTF_LTF_RSI_SHORT_MIN": "54", "MTF_VOLUME_SPIKE": "1.15",
        "MTF_SHORT_BODY_MULT": "1.2",
        "symbols": ["EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD"],
    },
}

BASE = {
    "STRATEGY": "mtf_trend_sweep", "RR_RATIO": "3.0",
    "ENABLE_SESSION_FILTER": "1", "SESSION_START_UTC": "7", "SESSION_END_UTC": "20",
    "MTF_REQUIRE_VOLUME": "1", "MTF_REQUIRE_REJECTION": "1",
}


def run(name: str, cfg: dict):
    sym_filter = cfg.pop("symbols", None)
    for k, v in {**BASE, **cfg}.items():
        os.environ[k] = str(v)

    paths = sorted(glob.glob("data/*_M5.csv"))
    total_trades = total_wins = 0
    total_net = 0.0
    max_days = 0
    for path in paths:
        sym = os.path.basename(path).replace("_M5.csv", "")
        if sym_filter and sym not in sym_filter:
            continue
        bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=sym)
        trades, _ = bt.run(path)
        if trades.empty:
            continue
        days = trades["entry_time"].dt.date.nunique()
        total_trades += len(trades)
        total_wins += (trades["net_pnl"] > 0).sum()
        total_net += float(trades["net_pnl"].sum())
        max_days = max(max_days, days)
    if total_trades:
        wr = total_wins / total_trades * 100
        tpd = total_trades / max_days if max_days else 0
        print(f"{name:14s} n={total_trades:3d} WR={wr:5.1f}% tpd={tpd:.2f} net={total_net:+.0f}")


if __name__ == "__main__":
    for n, c in PRESETS.items():
        run(n, c.copy())
