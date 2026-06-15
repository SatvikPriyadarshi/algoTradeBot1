"""Grid search for dry-run config: 1:3 RR, WR >= 45%, reasonable frequency."""

from __future__ import annotations

import os
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from backtester import StrategyBacktester

DATASETS = [
    ("EURUSD", "data/EURUSD_M5.csv"),
    ("GBPUSD", "data/GBPUSD_M5.csv"),
    ("XAUUSD", "data/XAUUSD_M5.csv"),
]

BASE = {
    "STRATEGY": "mtf_trend_sweep",
    "RR_RATIO": "3.0",
    "MAX_TRADES_PER_DAY": "0",
    "MTF_HTF_RULE": "4h",
    "MTF_LTF_SWING": "3",
    "MTF_LTF_LOOKBACK": "80",
    "ENABLE_SESSION_FILTER": "1",
    "SESSION_START_UTC": "7",
    "SESSION_END_UTC": "20",
}


def run_one(symbol: str, path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    bt = StrategyBacktester(risk_per_trade_cash=50, trade_capital_usd=500, symbol=symbol)
    trades, _ = bt.run(path)
    if trades.empty:
        return {"symbol": symbol, "trades": 0, "wr": 0.0, "net": 0.0, "days": 0, "tpd": 0.0}
    days = trades["entry_time"].dt.date.nunique()
    wr = (trades["net_pnl"] > 0).mean() * 100
    return {
        "symbol": symbol,
        "trades": len(trades),
        "wr": wr,
        "net": float(trades["net_pnl"].sum()),
        "days": days,
        "tpd": len(trades) / days if days else 0.0,
    }


def run_portfolio() -> dict:
    rows = [run_one(sym, path) for sym, path in DATASETS]
    rows = [r for r in rows if r]
    total_trades = sum(r["trades"] for r in rows)
    if total_trades == 0:
        return {"trades": 0, "wr": 0.0, "net": 0.0, "tpd": 0.0, "rows": rows}

    # Weighted WR across all trades (approximate via per-symbol)
    wins = sum(r["wr"] * r["trades"] / 100 for r in rows)
    wr = wins / total_trades * 100
    net = sum(r["net"] for r in rows)
    days = max(r["days"] for r in rows if r["days"] > 0) or 1
    tpd = total_trades / days
    return {"trades": total_trades, "wr": wr, "net": net, "tpd": tpd, "rows": rows}


def apply_cfg(cfg: dict):
    for k, v in {**BASE, **cfg}.items():
        os.environ[k] = str(v)


def main():
    grids = []
    for enable_fvg, enable_sd, h1_bias, adx, body, rsi in product(
        ("0", "1"),
        ("0", "1"),
        ("0", "1"),
        (16, 18, 20),
        (0.18, 0.22, 0.25),
        (45, 48),
    ):
        if enable_fvg == "0" and enable_sd == "0":
            pass  # sweep-only is valid
        grids.append({
            "MTF_ENABLE_FVG": enable_fvg,
            "MTF_ENABLE_SD": enable_sd,
            "MTF_REQUIRE_H1_BIAS": h1_bias,
            "MTF_MIN_HTF_ADX": str(adx),
            "MTF_MIN_BODY_ATR": str(body),
            "MTF_LTF_RSI_LONG_MAX": str(rsi),
            "MTF_LTF_RSI_SHORT_MIN": str(100 - rsi),
            "MTF_VOLUME_SPIKE": "1.15",
        })

    best = None
    candidates = []

    for i, cfg in enumerate(grids):
        apply_cfg(cfg)
        res = run_portfolio()
        if res["trades"] < 15:
            continue
        if res["wr"] >= 45 and res["tpd"] >= 0.8:
            candidates.append((res["wr"], res["tpd"], res["net"], cfg, res))
        if best is None or (res["wr"] >= 45 and res["wr"] > best[0]) or (
            res["wr"] >= 45 and res["wr"] == best[0] and res["tpd"] > best[1]
        ):
            if res["wr"] >= 40:
                best = (res["wr"], res["tpd"], res["net"], cfg, res)

        if (i + 1) % 20 == 0:
            print(f"  scanned {i + 1}/{len(grids)}...", flush=True)

    print("\n=== TOP CANDIDATES (WR>=45%, tpd>=0.8) ===")
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    for wr, tpd, net, cfg, res in candidates[:8]:
        print(f"WR={wr:.1f}% tpd={tpd:.2f} net={net:+.0f} | FVG={cfg['MTF_ENABLE_FVG']} SD={cfg['MTF_ENABLE_SD']} H1={cfg['MTF_REQUIRE_H1_BIAS']} ADX={cfg['MTF_MIN_HTF_ADX']} body={cfg['MTF_MIN_BODY_ATR']} rsi={cfg['MTF_LTF_RSI_LONG_MAX']}")

    if best:
        wr, tpd, net, cfg, res = best
        print(f"\n=== BEST OVERALL (WR>=40%) ===")
        print(f"WR={wr:.1f}% trades={res['trades']} tpd={tpd:.2f} net={net:+.0f}")
        for k, v in sorted(cfg.items()):
            print(f"  {k}={v}")
        for r in res["rows"]:
            print(f"  {r['symbol']}: n={r['trades']} WR={r['wr']:.1f}% tpd={r['tpd']:.2f}")
    else:
        print("No config met WR>=40% with 15+ trades")


if __name__ == "__main__":
    main()
