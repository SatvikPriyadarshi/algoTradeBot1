"""
Final Optimized Strategy — OptMeanRev on GBPUSD + AUDUSD.
Run: python run_final_optimized.py
"""
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

from systematic_backtest_engine import BacktestEngine, split_train_val_test
from strategies.opt_mean_rev import OptimizedMeanReversion

SYMBOLS = ["GBPUSD", "AUDUSD"]
DATA_DIR = Path("data")

FREQUENCY_TARGETS = {
    "GBPUSD": 3.0,
    "AUDUSD": 3.0,
}

SUCCESS_CRITERIA = {
    "win_rate_min": 0.45,
    "profit_factor_min": 1.15,
    "max_dd_max": 20.0,
    "frequency_tolerance": 0.25,
}


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


def run_strategy(strategy, symbol, data, engine):
    target_freq = FREQUENCY_TARGETS[symbol]
    _, _, test = split_train_val_test(data)
    trades, equity = engine.run_backtest(test, strategy, symbol)
    metrics = engine.calculate_metrics(trades, equity, symbol, target_freq)

    failure_reasons = []
    if metrics.win_rate < SUCCESS_CRITERIA["win_rate_min"]:
        failure_reasons.append(f"Win rate {metrics.win_rate:.1%} < 45%")
    if metrics.profit_factor < SUCCESS_CRITERIA["profit_factor_min"]:
        failure_reasons.append(f"PF {metrics.profit_factor:.2f} < 1.15")
    if metrics.max_drawdown_pct > SUCCESS_CRITERIA["max_dd_max"]:
        failure_reasons.append(f"DD {metrics.max_drawdown_pct:.1f}% > {SUCCESS_CRITERIA['max_dd_max']}%")

    freq_min = target_freq * (1 - SUCCESS_CRITERIA["frequency_tolerance"])
    freq_max = target_freq * (1 + SUCCESS_CRITERIA["frequency_tolerance"])
    if not (freq_min <= metrics.avg_trades_per_day <= freq_max):
        failure_reasons.append(
            f"Freq {metrics.avg_trades_per_day:.2f} not in [{freq_min:.2f}, {freq_max:.2f}]"
        )

    metrics.pass_criteria = len(failure_reasons) == 0
    metrics.failure_reasons = failure_reasons
    return metrics, trades, equity


def main():
    print("\n" + "=" * 90)
    print("OPTIMIZED MEAN REVERSION — GBPUSD + AUDUSD (M15)")
    print("=" * 90)

    engine = BacktestEngine(
        initial_capital=100000.0,
        position_size_pct=0.015,
        commission_per_lot=7.0,
        slippage_ticks=2.0,
    )

    strategy = OptimizedMeanReversion()
    results = {}

    for symbol in SYMBOLS:
        print(f"\n[{symbol}]")
        try:
            data = load_data(symbol)
            metrics, trades, equity = run_strategy(strategy, symbol, data, engine)
            results[symbol] = {"metrics": metrics, "trades": trades}
            status = "PASS" if metrics.pass_criteria else "FAIL"
            print(
                f"  {status} | {metrics.total_trades} trades | {metrics.avg_trades_per_day:.2f}/day | "
                f"WR={metrics.win_rate * 100:.1f}% | PF={metrics.profit_factor:.2f} | "
                f"DD={metrics.max_drawdown_pct:.1f}%"
            )
            for reason in metrics.failure_reasons:
                print(f"    - {reason}")
        except FileNotFoundError as exc:
            print(f"  SKIP: {exc}")

    print("\n" + "=" * 90)
    print("Live / dry-run: python engine.py --port 8000 --timeframe M15 --mode dry_run")
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
