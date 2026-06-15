"""
Strategy Comparison V2 - Improved strategies
Run: python run_compare_v2.py
"""
import pandas as pd
import numpy as np
from pathlib import Path
from systematic_backtest_engine import BacktestEngine, split_train_val_test
from systematic_strategies_v2 import (
    SessionBreakoutV2,
    MeanReversionV2,
    TrendFollowEMAV2,
    RangeBreakoutV2,
    MomentumBreakoutV2
)
import warnings
warnings.filterwarnings('ignore')


SYMBOLS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'AUDUSD']
DATA_DIR = Path('data')

FREQUENCY_TARGETS = {
    'XAUUSD': 2.5,
    'EURUSD': 3.0,
    'GBPUSD': 3.0,
    'AUDUSD': 3.0
}

SUCCESS_CRITERIA = {
    'win_rate_min': 0.45,
    'profit_factor_min': 1.15,
    'max_dd_max': 15.0,
    'frequency_tolerance': 0.20
}


def load_data(symbol: str, timeframe: str = 'M15') -> pd.DataFrame:
    m15_path = DATA_DIR / f'{symbol}_{timeframe}.csv'
    m5_path = DATA_DIR / f'{symbol}_M5.csv'
    
    if m15_path.exists():
        df = pd.read_csv(m15_path)
    elif m5_path.exists():
        print(f"  Resampling M5 to M15 for {symbol}...")
        df = pd.read_csv(m5_path)
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time').resample('15T').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'tick_volume': 'sum',
            'spread': 'mean'
        }).dropna().reset_index()
    else:
        raise FileNotFoundError(f"No data found for {symbol}")
    
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    
    if len(df) > 30000:
        df = df.iloc[-30000:].reset_index(drop=True)
    
    return df


def run_strategy_on_symbol(strategy, symbol, data, engine):
    target_freq = FREQUENCY_TARGETS[symbol]
    train, val, test = split_train_val_test(data)
    
    trades, equity = engine.run_backtest(test, strategy, symbol)
    metrics = engine.calculate_metrics(trades, equity, symbol, target_freq)
    
    return metrics, trades, equity


def print_summary_table(results):
    print("\n" + "=" * 130)
    print("RESULTS SUMMARY - V2 STRATEGIES (Out-of-Sample Test Set)")
    print("=" * 130)
    print(f"{'Strategy':<22} {'Symbol':<8} {'Trades':<7} {'T/Day':<7} {'Win%':<7} "
          f"{'PF':<7} {'DD%':<7} {'Return%':<9} {'STATUS':<8}")
    print("-" * 130)
    
    for key, data in results.items():
        strategy_name, symbol = key
        m = data['metrics']
        status = '✓ PASS' if m.pass_criteria else '✗ FAIL'
        
        print(f"{strategy_name:<22} {symbol:<8} {m.total_trades:<7} "
              f"{m.avg_trades_per_day:<7.2f} {m.win_rate*100:<7.1f} "
              f"{m.profit_factor:<7.2f} {m.max_drawdown_pct:<7.1f} "
              f"{m.total_return_pct:<9.1f} {status:<8}")
    
    print("=" * 130)


def analyze_v2_results(results):
    print("\n" + "=" * 100)
    print("V2 STRATEGY ANALYSIS")
    print("=" * 100)
    
    passing_strategies = {}
    for key, data in results.items():
        strategy_name, symbol = key
        if data['metrics'].pass_criteria:
            if strategy_name not in passing_strategies:
                passing_strategies[strategy_name] = []
            passing_strategies[strategy_name].append(symbol)
    
    print("\n✓ PASSING STRATEGIES:")
    print("-" * 100)
    if passing_strategies:
        for strat, symbols in passing_strategies.items():
            print(f"  {strat}: {', '.join(symbols)}")
    else:
        print("  None - all strategies failed at least one criterion")
    
    # Best per symbol
    print("\n BEST STRATEGY PER SYMBOL (by Profit Factor):")
    print("-" * 100)
    for symbol in SYMBOLS:
        symbol_results = [(k[0], v['metrics']) for k, v in results.items() if k[1] == symbol]
        symbol_results.sort(key=lambda x: x[1].profit_factor, reverse=True)
        
        if symbol_results:
            best_name, best_metrics = symbol_results[0]
            status = "✓" if best_metrics.pass_criteria else "✗"
            print(f"  {symbol}: {best_name:<22} | PF={best_metrics.profit_factor:.2f} | "
                  f"WR={best_metrics.win_rate*100:.1f}% | {status}")
    
    print("\n" + "=" * 100)


def main():
    print("\n" + "=" * 100)
    print("SYSTEMATIC STRATEGY COMPARISON V2")
    print("Improved entry/exit logic with relaxed filters")
    print("=" * 100)
    
    engine = BacktestEngine(
        initial_capital=100000.0,
        position_size_pct=0.02,
        commission_per_lot=7.0,
        slippage_ticks=2.0
    )
    
    strategies = [
        SessionBreakoutV2(),
        MeanReversionV2(),
        TrendFollowEMAV2(),
        RangeBreakoutV2(),
        MomentumBreakoutV2()
    ]
    
    results = {}
    
    for symbol in SYMBOLS:
        print(f"\n[{symbol}]")
        data = load_data(symbol)
        print(f"  Data: {len(data)} bars | {data.iloc[0]['time']} to {data.iloc[-1]['time']}")
        
        for strategy in strategies:
            try:
                metrics, trades, equity = run_strategy_on_symbol(
                    strategy, symbol, data, engine
                )
                results[(strategy.name, symbol)] = {
                    'metrics': metrics,
                    'trades': trades,
                    'equity': equity
                }
                
                status = "✓" if metrics.pass_criteria else "✗"
                print(f"  {strategy.name:<22} {status} | {metrics.total_trades:3d} trades | "
                      f"{metrics.avg_trades_per_day:.2f}/day | WR={metrics.win_rate*100:.1f}% | "
                      f"PF={metrics.profit_factor:.2f}")
            except Exception as e:
                print(f"  {strategy.name:<22} ERROR: {str(e)}")
    
    print_summary_table(results)
    analyze_v2_results(results)
    
    print("\nDISCLAIMER: Backtest results only - not predictive of future performance\n")


if __name__ == '__main__':
    main()
