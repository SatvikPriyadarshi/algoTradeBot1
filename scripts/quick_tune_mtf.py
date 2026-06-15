import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
from backtester import StrategyBacktester

data = "data/XAUUSD_M5.csv"
configs = [
    {"MTF_HTF_RULE": "1h", "MTF_LTF_SWING": 5, "MTF_LTF_RSI_LONG_MAX": 48, "MTF_LTF_RSI_SHORT_MIN": 52, "MTF_MIN_BODY_ATR": 0.2, "ENABLE_SESSION_FILTER": "1"},
    {"MTF_HTF_RULE": "4h", "MTF_LTF_SWING": 5, "MTF_LTF_RSI_LONG_MAX": 45, "MTF_LTF_RSI_SHORT_MIN": 55, "MTF_MIN_BODY_ATR": 0.25, "ENABLE_SESSION_FILTER": "1"},
    {"MTF_HTF_RULE": "4h", "MTF_LTF_SWING": 3, "MTF_LTF_RSI_LONG_MAX": 50, "MTF_LTF_RSI_SHORT_MIN": 50, "MTF_MIN_BODY_ATR": 0.15, "ENABLE_SESSION_FILTER": "0"},
    {"MTF_HTF_RULE": "1h", "MTF_LTF_SWING": 8, "MTF_LTF_RSI_LONG_MAX": 40, "MTF_LTF_RSI_SHORT_MIN": 60, "MTF_MIN_BODY_ATR": 0.3, "ENABLE_SESSION_FILTER": "1"},
    {"MTF_HTF_RULE": "4h", "MTF_LTF_SWING": 5, "MTF_HTF_RSI_LONG_MIN": 50, "MTF_HTF_RSI_LONG_MAX": 62, "MTF_HTF_RSI_SHORT_MIN": 38, "MTF_HTF_RSI_SHORT_MAX": 50, "MTF_MIN_BODY_ATR": 0.2, "ENABLE_SESSION_FILTER": "1"},
]

best = None
for cfg in configs:
    for k, v in cfg.items():
        os.environ[k] = str(v)
    os.environ["MAX_TRADES_PER_DAY"] = "0"
    bt = StrategyBacktester(risk_per_trade_cash=30, trade_capital_usd=500, symbol="XAUUSD", strategy_name="mtf_trend_sweep")
    t, _ = bt.run(data)
    if t.empty:
        print(cfg.get("MTF_HTF_RULE"), "NO TRADES")
        continue
    wr = (t["net_pnl"] > 0).mean() * 100
    net = float(t["net_pnl"].sum())
    print(f"htf={cfg.get('MTF_HTF_RULE')} swing={cfg.get('MTF_LTF_SWING')} trades={len(t)} WR={wr:.1f}% net={net:+.1f}")
    if len(t) >= 6 and (best is None or wr > best[0] or (wr == best[0] and net > best[1])):
        best = (wr, net, cfg, len(t))

print("BEST:", best)
