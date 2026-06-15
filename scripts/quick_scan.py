"""Fast config sweep — suppresses backtest logging."""

from __future__ import annotations

import logging
import os
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.disable(logging.CRITICAL)

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester

DATA = "data/EURUSD_M5.csv"
SYMBOL = "EURUSD"


def score(cfg: dict) -> dict:
    for k, v in cfg.items():
        os.environ[k] = str(v)
    os.environ.setdefault("STRATEGY", "mtf_trend_sweep")
    os.environ.setdefault("RR_RATIO", "3.0")
    os.environ.setdefault("MAX_TRADES_PER_DAY", "0")

    bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=SYMBOL)
    trades, _ = bt.run(DATA)
    if trades.empty:
        return {"trades": 0, "wr": 0.0, "tpd": 0.0, "net": 0.0, "cfg": cfg}

    days = trades["entry_time"].dt.date.nunique()
    wr = (trades["net_pnl"] > 0).mean() * 100
    return {
        "trades": len(trades),
        "wr": wr,
        "tpd": len(trades) / days if days else 0.0,
        "net": float(trades["net_pnl"].sum()),
        "cfg": cfg,
    }


def main():
    base = {
        "ENABLE_SESSION_FILTER": "1",
        "SESSION_START_UTC": "7",
        "SESSION_END_UTC": "20",
        "MTF_VOLUME_SPIKE": "1.15",
    }
    grids = []
    for fvg, sd, h1, daily, adx, body, rsi in product(
        ("0",),
        ("0", "1"),
        ("0", "1"),
        ("0", "1"),
        (16, 18, 20, 22),
        (0.18, 0.22, 0.25),
        (44, 46, 48),
    ):
        grids.append({
            **base,
            "MTF_ENABLE_FVG": fvg,
            "MTF_ENABLE_SD": sd,
            "MTF_REQUIRE_H1_BIAS": h1,
            "MTF_REQUIRE_DAILY_BIAS": daily,
            "MTF_MIN_HTF_ADX": str(adx),
            "MTF_MIN_BODY_ATR": str(body),
            "MTF_LTF_RSI_LONG_MAX": str(rsi),
            "MTF_LTF_RSI_SHORT_MIN": str(100 - rsi),
        })

    results = []
    for cfg in grids:
        results.append(score(cfg))

    hit = [r for r in results if r["trades"] >= 8 and r["wr"] >= 45]
    hit.sort(key=lambda x: (-x["wr"], -x["tpd"], -x["net"]))

    print("=== WR >= 45% (min 8 trades) ===")
    for r in hit[:12]:
        c = r["cfg"]
        print(
            f"WR={r['wr']:.1f}% n={r['trades']} tpd={r['tpd']:.2f} net={r['net']:+.0f} | "
            f"SD={c['MTF_ENABLE_SD']} H1={c['MTF_REQUIRE_H1_BIAS']} D={c['MTF_REQUIRE_DAILY_BIAS']} "
            f"ADX={c['MTF_MIN_HTF_ADX']} body={c['MTF_MIN_BODY_ATR']} rsi={c['MTF_LTF_RSI_LONG_MAX']}"
        )

    if not hit:
        near = sorted(
            [r for r in results if r["trades"] >= 8],
            key=lambda x: (-x["wr"], -x["tpd"]),
        )[:8]
        print("\n=== BEST NEAR MISS ===")
        for r in near:
            c = r["cfg"]
            print(
                f"WR={r['wr']:.1f}% n={r['trades']} tpd={r['tpd']:.2f} | "
                f"SD={c['MTF_ENABLE_SD']} H1={c['MTF_REQUIRE_H1_BIAS']} D={c['MTF_REQUIRE_DAILY_BIAS']} "
                f"ADX={c['MTF_MIN_HTF_ADX']} body={c['MTF_MIN_BODY_ATR']} rsi={c['MTF_LTF_RSI_LONG_MAX']}"
            )


if __name__ == "__main__":
    main()
