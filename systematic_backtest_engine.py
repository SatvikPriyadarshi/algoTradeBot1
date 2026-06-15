"""
Systematic Trading Strategy Backtesting Engine
Supports walk-forward validation with proper OOS testing
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
import warnings
warnings.filterwarnings('ignore')


@dataclass
class TradeResult:
    """Single trade outcome"""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str  # 'LONG' or 'SHORT'
    entry_price: float
    exit_price: float
    size: float
    gross_pnl: float
    net_pnl: float
    costs: float
    reason: str
    hold_bars: int


@dataclass
class PerformanceMetrics:
    """Strategy performance metrics"""
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_trades_per_day: float
    expectancy_per_trade: float
    avg_hold_bars: float
    total_return_pct: float
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    pass_criteria: bool
    failure_reasons: List[str]


class SystematicStrategy(ABC):
    """Base class for all systematic strategies"""
    
    def __init__(self, name: str, direction_bias: str = 'BOTH'):
        """
        Args:
            name: Strategy identifier
            direction_bias: 'LONG', 'SHORT', or 'BOTH'
        """
        self.name = name
        self.direction_bias = direction_bias
    
    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals for entire dataframe
        Returns df with 'signal' column: 1=LONG, -1=SHORT, 0=HOLD
        """
        pass
    
    @abstractmethod
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        """
        Calculate exit point for a trade
        Returns: (exit_idx, exit_price, exit_reason)
        """
        pass


class BacktestEngine:
    """Vectorized backtesting engine with realistic cost model"""
    
    def __init__(self, 
                 initial_capital: float = 100000.0,
                 position_size_pct: float = 0.02,  # 2% risk per trade
                 commission_per_lot: float = 7.0,
                 slippage_ticks: float = 2.0):
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.commission_per_lot = commission_per_lot
        self.slippage_ticks = slippage_ticks
        
        # Symbol specifications
        self.symbol_specs = {
            'XAUUSD': {'tick_size': 0.01, 'tick_value': 0.01, 'contract_size': 100, 
                      'spread_points': 30, 'point_value': 1.0},
            'EURUSD': {'tick_size': 0.00001, 'tick_value': 1.0, 'contract_size': 100000,
                      'spread_pips': 1.0, 'pip_value': 10.0},
            'GBPUSD': {'tick_size': 0.00001, 'tick_value': 1.0, 'contract_size': 100000,
                      'spread_pips': 1.0, 'pip_value': 10.0},
            'AUDUSD': {'tick_size': 0.00001, 'tick_value': 1.0, 'contract_size': 100000,
                      'spread_pips': 1.0, 'pip_value': 10.0},
        }
    
    def calculate_costs(self, symbol: str, entry_price: float, size: float) -> float:
        """Calculate total trading costs (spread + commission + slippage)"""
        specs = self.symbol_specs[symbol]
        
        # Commission
        commission = self.commission_per_lot * size
        
        # Spread cost
        if symbol == 'XAUUSD':
            spread_cost = specs['spread_points'] * specs['point_value'] * size
        else:
            spread_cost = specs['spread_pips'] * specs['pip_value'] * size
        
        # Slippage (both entry and exit)
        if symbol == 'XAUUSD':
            slippage = self.slippage_ticks * 2 * specs['point_value'] * size
        else:
            slippage = self.slippage_ticks * 2 * 0.1 * size  # 0.1 pips per tick
        
        return commission + spread_cost + slippage
    
    def calculate_position_size(self, symbol: str, capital: float, 
                               entry_price: float, stop_loss: float) -> float:
        """Calculate position size based on risk management"""
        specs = self.symbol_specs[symbol]
        risk_amount = capital * self.position_size_pct
        
        # Calculate risk per unit
        if symbol == 'XAUUSD':
            risk_per_unit = abs(entry_price - stop_loss) * specs['point_value']
        else:
            pips_risk = abs(entry_price - stop_loss) / 0.0001
            risk_per_unit = pips_risk * specs['pip_value'] / specs['contract_size']
        
        if risk_per_unit == 0:
            return 0.0
        
        # Size in lots
        size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
        return max(0.01, min(size, 10.0))  # Min 0.01, max 10 lots
    
    def run_backtest(self, df: pd.DataFrame, strategy: SystematicStrategy, 
                     symbol: str) -> Tuple[List[TradeResult], pd.DataFrame]:
        """
        Execute backtest on dataset
        Entry on next bar open after signal at bar close
        """
        df = df.copy().reset_index(drop=True)
        df = strategy.generate_signals(df)
        
        trades = []
        equity_curve = []
        current_capital = self.initial_capital
        in_position = False
        position_info = None
        
        for i in range(1, len(df)):  # Start at 1 to have previous bar
            current_bar = df.iloc[i]
            prev_bar = df.iloc[i-1]
            
            # Check for exit if in position
            if in_position:
                exit_idx, exit_price, exit_reason = strategy.calculate_exit(
                    df, position_info['entry_idx'], position_info['direction'], 
                    position_info['entry_price']
                )
                
                if exit_idx <= i:
                    # Close position
                    gross_pnl = self._calculate_pnl(
                        position_info['entry_price'], exit_price,
                        position_info['direction'], position_info['size'], symbol
                    )
                    costs = self.calculate_costs(symbol, position_info['entry_price'], 
                                                 position_info['size'])
                    net_pnl = gross_pnl - costs
                    current_capital += net_pnl
                    
                    trade = TradeResult(
                        entry_time=position_info['entry_time'],
                        exit_time=df.iloc[exit_idx]['time'],
                        direction='LONG' if position_info['direction'] == 1 else 'SHORT',
                        entry_price=position_info['entry_price'],
                        exit_price=exit_price,
                        size=position_info['size'],
                        gross_pnl=gross_pnl,
                        net_pnl=net_pnl,
                        costs=costs,
                        reason=exit_reason,
                        hold_bars=exit_idx - position_info['entry_idx']
                    )
                    trades.append(trade)
                    in_position = False
                    position_info = None
            
            # Check for new entry signal (signal generated at bar close, enter at next open)
            if not in_position and prev_bar['signal'] != 0:
                direction = int(prev_bar['signal'])
                
                # Filter by direction bias
                if strategy.direction_bias == 'LONG' and direction == -1:
                    continue
                elif strategy.direction_bias == 'SHORT' and direction == 1:
                    continue
                
                # Enter at current bar open (next bar after signal)
                entry_price = current_bar['open']
                
                # Calculate stop loss based on strategy (simplified: use ATR-based)
                atr = df.iloc[i-20:i]['high'].sub(df.iloc[i-20:i]['low']).mean() if i >= 20 else entry_price * 0.02
                stop_loss = entry_price - (2 * atr if direction == 1 else -2 * atr)
                
                size = self.calculate_position_size(symbol, current_capital, 
                                                   entry_price, stop_loss)
                
                if size > 0:
                    position_info = {
                        'entry_idx': i,
                        'entry_time': current_bar['time'],
                        'entry_price': entry_price,
                        'direction': direction,
                        'size': size,
                        'stop_loss': stop_loss
                    }
                    in_position = True
            
            # Track equity
            floating_pnl = 0.0
            if in_position:
                floating_pnl = self._calculate_pnl(
                    position_info['entry_price'], current_bar['close'],
                    position_info['direction'], position_info['size'], symbol
                )
            
            equity_curve.append({
                'time': current_bar['time'],
                'capital': current_capital,
                'equity': current_capital + floating_pnl
            })
        
        equity_df = pd.DataFrame(equity_curve)
        return trades, equity_df
    
    def _calculate_pnl(self, entry: float, exit: float, direction: int, 
                       size: float, symbol: str) -> float:
        """Calculate gross P&L before costs"""
        specs = self.symbol_specs[symbol]
        
        if symbol == 'XAUUSD':
            pnl = (exit - entry) * direction * specs['contract_size'] * size
        else:
            pips = (exit - entry) / 0.0001 * direction
            pnl = pips * specs['pip_value'] * size
        
        return pnl
    
    def calculate_metrics(self, trades: List[TradeResult], equity_df: pd.DataFrame,
                         symbol: str, target_trades_per_day: float) -> PerformanceMetrics:
        """Calculate comprehensive performance metrics"""
        if not trades:
            return PerformanceMetrics(
                total_trades=0, win_rate=0, profit_factor=0, max_drawdown_pct=100,
                sharpe_ratio=0, avg_trades_per_day=0, expectancy_per_trade=0,
                avg_hold_bars=0, total_return_pct=0, winning_trades=0, losing_trades=0,
                avg_win=0, avg_loss=0, largest_win=0, largest_loss=0,
                pass_criteria=False, failure_reasons=['No trades executed']
            )
        
        # Basic stats
        total_trades = len(trades)
        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]
        
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        
        gross_profit = sum(t.net_pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.net_pnl for t in losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (np.inf if gross_profit > 0 else 0)
        
        # Drawdown
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['drawdown'].max()
        
        # Sharpe (annualized)
        if len(equity_df) > 1:
            equity_df['returns'] = equity_df['equity'].pct_change()
            sharpe = equity_df['returns'].mean() / equity_df['returns'].std() * np.sqrt(252 * 24 * 4) if equity_df['returns'].std() > 0 else 0
        else:
            sharpe = 0
        
        # Trading frequency
        first_trade = min(t.entry_time for t in trades)
        last_trade = max(t.exit_time for t in trades)
        days = (last_trade - first_trade).days + 1
        avg_trades_per_day = total_trades / days if days > 0 else 0
        
        # Other metrics
        expectancy = sum(t.net_pnl for t in trades) / total_trades
        avg_hold = sum(t.hold_bars for t in trades) / total_trades
        total_return = ((equity_df['equity'].iloc[-1] - self.initial_capital) / self.initial_capital * 100)
        
        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0
        largest_win = max((t.net_pnl for t in wins), default=0)
        largest_loss = min((t.net_pnl for t in losses), default=0)
        
        # Check success criteria
        failure_reasons = []
        if win_rate < 0.45:
            failure_reasons.append(f'Win rate {win_rate:.1%} < 45%')
        if profit_factor < 1.15:
            failure_reasons.append(f'Profit factor {profit_factor:.2f} < 1.15')
        if max_dd > 15:
            failure_reasons.append(f'Max DD {max_dd:.1f}% > 15%')
        
        # Frequency check (±20% tolerance)
        freq_min = target_trades_per_day * 0.8
        freq_max = target_trades_per_day * 1.2
        if not (freq_min <= avg_trades_per_day <= freq_max):
            failure_reasons.append(
                f'Frequency {avg_trades_per_day:.2f} not in [{freq_min:.2f}, {freq_max:.2f}]'
            )
        
        pass_criteria = len(failure_reasons) == 0
        
        return PerformanceMetrics(
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            avg_trades_per_day=avg_trades_per_day,
            expectancy_per_trade=expectancy,
            avg_hold_bars=avg_hold,
            total_return_pct=total_return,
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            pass_criteria=pass_criteria,
            failure_reasons=failure_reasons
        )


def split_train_val_test(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split data into train/val/test sets: 60/20/20"""
    n = len(df)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    
    train = df.iloc[:train_end].copy()
    val = df.iloc[train_end:val_end].copy()
    test = df.iloc[val_end:].copy()
    
    return train, val, test
