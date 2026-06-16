import warnings
from pathlib import Path
import pandas as pd
warnings.filterwarnings("ignore")

from systematic_backtest_engine import BacktestEngine, split_train_val_test
from candidate_strategies import Candidate10_HolyGrail

SYMBOLS = ["GBPUSD", "EURUSD", "XAUUSD"]
DATA_DIR = Path("data")

def load_data(symbol: str, timeframe: str = "M15") -> pd.DataFrame:
    m15_path = DATA_DIR / f"{symbol}_{timeframe}.csv"
    m5_path = DATA_DIR / f"{symbol}_M5.csv"

    if m15_path.exists():
        df = pd.read_csv(m15_path)
    elif m5_path.exists():
        df = pd.read_csv(m5_path)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").resample("15min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "tick_volume": "sum", "spread": "mean",
        }).dropna().reset_index()
    else:
        raise FileNotFoundError(f"No data for {symbol}")

    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)

def run_strategy(strategy, symbol, data, engine, target_freq=2.0):
    # Run on all data instead of splitting just to see enough trades
    trades, equity = engine.run_backtest(data, strategy, symbol)
    metrics = engine.calculate_metrics(trades, equity, symbol, target_freq)
    
    # Custom criteria per user prompt
    # target A: >=45% WR
    # target B: >=65% WR
    
    failure_reasons = []
    if metrics.win_rate < 0.45:
        failure_reasons.append(f"Win rate {metrics.win_rate:.1%} < 45%")
    if metrics.profit_factor < 1.1:
        failure_reasons.append(f"PF {metrics.profit_factor:.2f} < 1.1")
    if metrics.max_drawdown_pct > 20.0:
        failure_reasons.append(f"DD {metrics.max_drawdown_pct:.1f}% > 20%")
    if metrics.avg_trades_per_day < 2.0:
        failure_reasons.append(f"Freq {metrics.avg_trades_per_day:.2f} < 2.0/day minimum")

    metrics.pass_criteria = len(failure_reasons) == 0
    metrics.failure_reasons = failure_reasons
    return metrics, trades, equity

def main():
    print("=" * 90)
    print("CANDIDATE STRATEGIES TEST RUN")
    print("=" * 90)

    engine = BacktestEngine(
        initial_capital=100000.0,
        position_size_pct=0.015,
        commission_per_lot=7.0,
        slippage_ticks=2.0,
    )

    strategies = [
        Candidate10_HolyGrail()
    ]

    for strategy in strategies:
        print(f"\nEvaluating: {strategy.name}")
        print("-" * 60)
        
        for symbol in SYMBOLS:
            try:
                data = load_data(symbol, "M15")
                metrics, trades, equity = run_strategy(strategy, symbol, data, engine)
                status = "PASS" if metrics.pass_criteria else "FAIL"
                
                print(
                    f"  [{symbol}] {status} | {metrics.total_trades} trades | {metrics.avg_trades_per_day:.2f}/day | "
                    f"WR={metrics.win_rate:.1%} | PF={metrics.profit_factor:.2f} | DD={metrics.max_drawdown_pct:.1f}%"
                )
                if status == "FAIL":
                    for reason in metrics.failure_reasons:
                        print(f"    - {reason}")
            except Exception as e:
                print(f"  [{symbol}] Error: {e}")

if __name__ == "__main__":
    main()
