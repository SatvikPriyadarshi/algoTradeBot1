import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
from backtester import StrategyBacktester

data = "data/XAUUSD_M5.csv"
base = {
    "MTF_HTF_RULE": "4h",
    "MTF_LTF_SWING": 3,
    "MTF_LTF_RSI_LONG_MAX": 50,
    "MTF_LTF_RSI_SHORT_MIN": 50,
    "MTF_MIN_BODY_ATR": 0.15,
    "MAX_TRADES_PER_DAY": "0",
}
grids = []
for session in ("0", "1"):
    for body in (0.12, 0.15, 0.18):
        for lrsi in (48, 50, 52):
            for htf_lmin in (45, 48, 50):
                grids.append({**base, "ENABLE_SESSION_FILTER": session, "MTF_MIN_BODY_ATR": body,
                              "MTF_LTF_RSI_LONG_MAX": lrsi, "MTF_LTF_RSI_SHORT_MIN": 100 - lrsi,
                              "MTF_HTF_RSI_LONG_MIN": htf_lmin, "MTF_HTF_RSI_LONG_MAX": 65,
                              "MTF_HTF_RSI_SHORT_MIN": 35, "MTF_HTF_RSI_SHORT_MAX": 100 - htf_lmin})

best = None
for cfg in grids:
    for k, v in cfg.items():
        os.environ[k] = str(v)
    bt = StrategyBacktester(risk_per_trade_cash=30, trade_capital_usd=500, symbol="XAUUSD", strategy_name="mtf_trend_sweep")
    t, _ = bt.run(data)
    if t.empty or len(t) < 8:
        continue
    wr = (t["net_pnl"] > 0).mean() * 100
    net = float(t["net_pnl"].sum())
    if best is None or wr > best[0] or (wr == best[0] and net > best[1]):
        best = (wr, net, len(t), cfg)

if best:
    print(f"BEST WR={best[0]:.1f}% net={best[1]:+.1f} trades={best[2]}")
    for k, v in sorted(best[3].items()):
        print(f"  {k}={v}")
else:
    print("No config with 8+ trades")
