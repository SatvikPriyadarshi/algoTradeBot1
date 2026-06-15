"""
Main script to compare all systematic trading strategies
Run: python run_compare.py
"""
import pandas as pd
import numpy as np
from pathlib import Path
from systematic_backtest_engine import BacktestEngine, split_train_val_test
from systematic_strategies import (
    SessionBreakoutStrategy,
    MeanReversionVWAPStrategy,
    TrendPullbackStrategy,
    VolatilityBreakoutStrategy,
    LondonOpenRangeStrategy,
    GoldMomentumStrategy
)
import warnings
warnings.filterwarnings('ignore')


# Configuration
SYMBOLS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'AUDUSD']
DATA_DIR = Path('data')

# Trade frequency targets
FREQUENCY_TARGETS = {
    'XAUUSD': 2.5,  # 2-3 trades/day
    'EURUSD': 3.0,
    'GBPUSD': 3.0,
    'AUDUSD': 3.0
}

# Success criteria thresholds
SUCCESS_CRITERIA = {
    'win_rate_min': 0.45,
    'profit_factor_min': 1.15,
    'max_dd_max': 15.0,
    'frequency_tolerance': 0.20  # ±20%
}


def load_data(symbol: str, timeframe: str = 'M15') -> pd.DataFrame:
    """Load and prepare market data"""
    # Try M15 first, fallback to M5 and resample
    m15_path = DATA_DIR / f'{symbol}_{timeframe}.csv'
    m5_path = DATA_DIR / f'{symbol}_M5.csv'
    
    if m15_path.exists():
        df = pd.read_csv(m15_path)
    elif m5_path.exists():
        print(f"  M15 not found for {symbol}, resampling M5 data...")
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
    
    # Keep only recent data for faster processing (approx 10 months)
    if len(df) > 30000:
        df = df.iloc[-30000:].reset_index(drop=True)
    
    return df


def run_strategy_on_symbol(strategy, symbol, data, engine):
    """Run single strategy on single symbol with OOS testing"""
    target_freq = FREQUENCY_TARGETS[symbol]
    
    # Split data
    train, val, test = split_train_val_test(data)
    
    print(f"    Train: {train.iloc[0]['time']} to {train.iloc[-1]['time']} ({len(train)} bars)")
    print(f"    Test:  {test.iloc[0]['time']} to {test.iloc[-1]['time']} ({len(test)} bars)")
    
    # Run backtest on test set (OOS)
    trades, equity = engine.run_backtest(test, strategy, symbol)
    metrics = engine.calculate_metrics(trades, equity, symbol, target_freq)
    
    return metrics, trades, equity


def print_results_table(results):
    """Print comparison table"""
    print("\n" + "=" * 140)
    print("OUT-OF-SAMPLE TEST RESULTS (Test Set = 20% of data)")
    print("=" * 140)
    print(f"{'Strategy':<25} {'Symbol':<8} {'Trades':<7} {'T/Day':<7} {'Win%':<7} "
          f"{'PF':<7} {'DD%':<7} {'Sharpe':<7} {'Expect':<10} {'PASS':<6}")
    print("-" * 140)
    
    for key, data in results.items():
        strategy_name, symbol = key
        m = data['metrics']
        status = '✓ PASS' if m.pass_criteria else '✗ FAIL'
        
        print(f"{strategy_name:<25} {symbol:<8} {m.total_trades:<7} "
              f"{m.avg_trades_per_day:<7.2f} {m.win_rate*100:<7.1f} "
              f"{m.profit_factor:<7.2f} {m.max_drawdown_pct:<7.1f} "
              f"{m.sharpe_ratio:<7.2f} ${m.expectancy_per_trade:<9.0f} {status:<6}")
    
    print("=" * 140)


def analyze_results(results):
    """Analyze and recommend best strategies"""
    print("\n" + "=" * 100)
    print("ANALYSIS & RECOMMENDATIONS")
    print("=" * 100)
    
    # Find passing strategies per symbol
    passing_by_symbol = {}
    for symbol in SYMBOLS:
        passing = []
        for key, data in results.items():
            strategy_name, sym = key
            if sym == symbol and data['metrics'].pass_criteria:
                passing.append((strategy_name, data['metrics']))
        passing_by_symbol[symbol] = passing
    
    # Print winners per symbol
    print("\n1. WINNERS PER SYMBOL:")
    print("-" * 100)
    for symbol in SYMBOLS:
        print(f"\n{symbol}:")
        if passing_by_symbol[symbol]:
            # Sort by profit factor
            sorted_strats = sorted(passing_by_symbol[symbol], 
                                  key=lambda x: x[1].profit_factor, reverse=True)
            for strategy_name, metrics in sorted_strats[:3]:
                print(f"  ✓ {strategy_name:25} | PF={metrics.profit_factor:.2f} | "
                      f"WR={metrics.win_rate*100:.1f}% | Trades/day={metrics.avg_trades_per_day:.2f}")
        else:
            print("  ✗ No strategies passed all criteria")
            # Show closest
            closest = []
            for key, data in results.items():
                strategy_name, sym = key
                if sym == symbol:
                    fail_count = len(data['metrics'].failure_reasons)
                    closest.append((strategy_name, data['metrics'], fail_count))
            closest.sort(key=lambda x: x[2])
            if closest:
                strategy_name, metrics, fails = closest[0]
                print(f"  ~ Best attempt: {strategy_name} (failed {fails} criteria)")
                for reason in metrics.failure_reasons:
                    print(f"      - {reason}")
    
    # Portfolio recommendation
    print("\n2. PORTFOLIO RECOMMENDATION:")
    print("-" * 100)
    
    # Count which strategies pass on most symbols
    strategy_scores = {}
    for key, data in results.items():
        strategy_name, symbol = key
        if strategy_name not in strategy_scores:
            strategy_scores[strategy_name] = {'pass': 0, 'total_pf': 0, 'symbols': []}
        if data['metrics'].pass_criteria:
            strategy_scores[strategy_name]['pass'] += 1
            strategy_scores[strategy_name]['total_pf'] += data['metrics'].profit_factor
            strategy_scores[strategy_name]['symbols'].append(symbol)
    
    if strategy_scores:
        best_strategy = max(strategy_scores.items(), 
                           key=lambda x: (x[1]['pass'], x[1]['total_pf']))
        name, info = best_strategy
        print(f"\nBest Overall Strategy: {name}")
        print(f"  Passed on {info['pass']}/4 symbols: {', '.join(info['symbols'])}")
        print(f"  Avg Profit Factor: {info['total_pf']/len(SYMBOLS):.2f}")
    
    # Why winners work and losers fail
    print("\n3. WHY STRATEGIES SUCCEED/FAIL:")
    print("-" * 100)
    
    # Aggregate failure reasons
    failure_stats = {}
    for key, data in results.items():
        strategy_name, symbol = key
        if not data['metrics'].pass_criteria:
            for reason in data['metrics'].failure_reasons:
                if reason not in failure_stats:
                    failure_stats[reason] = []
                failure_stats[reason].append(f"{strategy_name}/{symbol}")
    
    if failure_stats:
        print("\nCommon Failure Modes:")
        for reason, instances in sorted(failure_stats.items(), 
                                       key=lambda x: len(x[1]), reverse=True):
            print(f"  • {reason} ({len(instances)} instances)")
            if len(instances) <= 6:
                for inst in instances[:6]:
                    print(f"      - {inst}")
    
    # Success patterns
    print("\nSuccess Patterns:")
    success_patterns = {
        'SessionBreakout': 'Works on volatile sessions with clear directional moves',
        'MeanRevVWAP': 'Effective in ranging markets during London/NY overlap',
        'TrendPullback': 'Captures trend continuation after healthy corrections',
        'VolBreakout': 'Profits from volatility expansion after compression',
        'LondonORB': 'Exploits London open momentum and liquidity',
        'GoldNYMomentum': 'Capitalizes on gold volatility during US session'
    }
    
    for strategy_name in strategy_scores:
        if strategy_scores[strategy_name]['pass'] > 0:
            print(f"  ✓ {strategy_name}: {success_patterns.get(strategy_name, 'N/A')}")
    
    print("\n" + "=" * 100)


def main():
    print("\n" + "=" * 100)
    print("SYSTEMATIC TRADING STRATEGY COMPARISON")
    print("=" * 100)
    print("\nCONFIGURATION:")
    print(f"  Universe: {', '.join(SYMBOLS)}")
    print(f"  Timeframe: M15 (15-minute bars)")
    print(f"  Data split: 60% train / 20% val / 20% test")
    print(f"  Testing on: Out-of-sample test set only")
    print(f"  Cost model: XAU=30pts, Majors=1.0pip spread + $7 commission + 2 ticks slippage")
    print("\nSUCCESS CRITERIA (all must pass on OOS):")
    print(f"  • Win rate ≥ {SUCCESS_CRITERIA['win_rate_min']*100:.0f}%")
    print(f"  • Profit factor ≥ {SUCCESS_CRITERIA['profit_factor_min']}")
    print(f"  • Max drawdown ≤ {SUCCESS_CRITERIA['max_dd_max']:.0f}%")
    print(f"  • Trade frequency: XAUUSD=2-3/day, Majors≥3/day (±20% tolerance)")
    
    # Initialize engine
    engine = BacktestEngine(
        initial_capital=100000.0,
        position_size_pct=0.02,  # 2% risk per trade
        commission_per_lot=7.0,
        slippage_ticks=2.0
    )
    
    # Define strategies to test
    strategies = [
        SessionBreakoutStrategy(),
        MeanReversionVWAPStrategy(),
        TrendPullbackStrategy(),
        VolatilityBreakoutStrategy(),
        LondonOpenRangeStrategy(),
        GoldMomentumStrategy()
    ]
    
    # Store all results
    results = {}
    
    # Run backtests
    print("\n" + "=" * 100)
    print("RUNNING BACKTESTS...")
    print("=" * 100)
    
    for symbol in SYMBOLS:
        print(f"\n[{symbol}]")
        
        # Load data
        print("  Loading data...")
        data = load_data(symbol)
        print(f"  Loaded {len(data)} bars from {data.iloc[0]['time']} to {data.iloc[-1]['time']}")
        
        # Test each strategy
        for strategy in strategies:
            print(f"\n  Testing {strategy.name}...")
            
            try:
                metrics, trades, equity = run_strategy_on_symbol(
                    strategy, symbol, data, engine
                )
                results[(strategy.name, symbol)] = {
                    'metrics': metrics,
                    'trades': trades,
                    'equity': equity
                }
                
                status = "PASS ✓" if metrics.pass_criteria else "FAIL ✗"
                print(f"    Result: {status} | {metrics.total_trades} trades | "
                      f"{metrics.avg_trades_per_day:.2f} trades/day | "
                      f"WR={metrics.win_rate*100:.1f}% | PF={metrics.profit_factor:.2f}")
                
                if not metrics.pass_criteria:
                    for reason in metrics.failure_reasons[:2]:
                        print(f"      └─ {reason}")
            
            except Exception as e:
                print(f"    ERROR: {str(e)}")
                continue
    
    # Print results
    print_results_table(results)
    analyze_results(results)
    
    # Summary
    print("\n" + "=" * 100)
    print("EXECUTION COMPLETE")
    print("=" * 100)
    print("\nTo run this script: python run_compare.py")
    print("\nFiles created:")
    print("  • systematic_backtest_engine.py - Core backtesting engine")
    print("  • systematic_strategies.py - Strategy implementations")
    print("  • run_compare.py - This comparison script")
    
    print("\nDISCLAIMER:")
    print("  These are backtested results on historical data.")
    print("  Past performance does NOT guarantee future results.")
    print("  Use for research and education only - not live trading advice.")
    print("\n" + "=" * 100 + "\n")


if __name__ == '__main__':
    main()
