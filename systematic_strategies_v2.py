"""
Improved Systematic Trading Strategies - Version 2
Adjusted entry/exit logic for better frequency and performance
"""
import pandas as pd
import numpy as np
from systematic_backtest_engine import SystematicStrategy
from typing import Tuple


class SessionBreakoutV2(SystematicStrategy):
    """
    Improved Session Breakout Strategy
    Changes: Tighter stops, better filters, accept both session breakouts
    """
    
    def __init__(self, name: str = "SessionBreakoutV2"):
        super().__init__(name, direction_bias='BOTH')
        self.atr_period = 14
        self.rr_ratio = 2.5  # Increased from 2.0
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # ATR
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = df['tr'].rolling(self.atr_period).mean()
        
        # Session
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = (df['hour'] >= 7) & (df['hour'] < 20)
        
        # Dynamic range (shorter window for more signals)
        window = 12  # 3 hours
        df['range_high'] = df['high'].rolling(window).max()
        df['range_low'] = df['low'].rolling(window).min()
        
        # Breakout with momentum confirmation
        df['breakout_up'] = (df['close'] > df['range_high'].shift(1)) & \
                           (df['close'] > df['open']) & df['in_session']
        df['breakout_down'] = (df['close'] < df['range_low'].shift(1)) & \
                             (df['close'] < df['open']) & df['in_session']
        
        # Less strict ATR filter
        df['atr_ok'] = df['atr'] > df['atr'].rolling(100).quantile(0.3)
        
        df['signal'] = 0
        df.loc[df['breakout_up'] & df['atr_ok'], 'signal'] = 1
        df.loc[df['breakout_down'] & df['atr_ok'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        atr = df.iloc[entry_idx]['atr']
        stop_loss = entry_price - (direction * atr * 1.2)  # Tighter stop
        take_profit = entry_price + (direction * atr * self.rr_ratio)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Trailing stop after 1:1
            break_even = entry_price + (direction * atr * 1.2)
            if direction == 1 and bar['high'] >= break_even:
                if bar['low'] <= entry_price + (atr * 0.3):
                    return i, entry_price + (atr * 0.3), 'Trail Stop'
            if direction == -1 and bar['low'] <= break_even:
                if bar['high'] >= entry_price - (atr * 0.3):
                    return i, entry_price - (atr * 0.3), 'Trail Stop'
            
            if i - entry_idx >= 32:  # 8 hours max
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class MeanReversionV2(SystematicStrategy):
    """
    Improved Mean Reversion - simpler VWAP with relaxed entry
    """
    
    def __init__(self, name: str = "MeanReversionV2"):
        super().__init__(name, direction_bias='BOTH')
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Simple moving average as mean
        df['ma20'] = df['close'].rolling(20).mean()
        df['std'] = df['close'].rolling(20).std()
        
        # Bands
        df['upper'] = df['ma20'] + (df['std'] * 1.8)  # Relaxed from 2.0
        df['lower'] = df['ma20'] - (df['std'] * 1.8)
        
        # Session
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = (df['hour'] >= 7) & (df['hour'] < 20)
        
        # Entry conditions - much simpler
        df['oversold'] = df['close'] < df['lower']
        df['overbought'] = df['close'] > df['upper']
        
        df['signal'] = 0
        df.loc[df['oversold'] & df['in_session'], 'signal'] = 1
        df.loc[df['overbought'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        entry_bar = df.iloc[entry_idx]
        ma = entry_bar['ma20']
        std = entry_bar['std']
        
        stop_loss = entry_price - (direction * std * 2.5)
        take_profit = ma  # Target mean reversion
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Exit near mean
            if direction == 1 and bar['high'] >= take_profit * 0.995:
                return i, min(bar['high'], take_profit), 'Mean Reached'
            if direction == -1 and bar['low'] <= take_profit * 1.005:
                return i, max(bar['low'], take_profit), 'Mean Reached'
            
            if i - entry_idx >= 24:  # 6 hours max
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class TrendFollowEMAV2(SystematicStrategy):
    """
    Simplified Trend Follow with EMA - relaxed conditions
    """
    
    def __init__(self, name: str = "TrendFollowEMAV2"):
        super().__init__(name, direction_bias='BOTH')
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # EMAs
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # Simple trend
        df['uptrend'] = df['ema20'] > df['ema50']
        df['downtrend'] = df['ema20'] < df['ema50']
        
        # Pullback to EMA
        df['near_ema_long'] = (df['low'] <= df['ema20'] * 1.002) & df['uptrend']
        df['near_ema_short'] = (df['high'] >= df['ema20'] * 0.998) & df['downtrend']
        
        # Bounce confirmation
        df['bounce_up'] = df['close'] > df['open']
        df['bounce_down'] = df['close'] < df['open']
        
        # Session
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = (df['hour'] >= 7) & (df['hour'] < 20)
        
        df['signal'] = 0
        df.loc[df['near_ema_long'] & df['bounce_up'] & df['in_session'], 'signal'] = 1
        df.loc[df['near_ema_short'] & df['bounce_down'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        # ATR-based
        atr = df.iloc[entry_idx-20:entry_idx]['high'].sub(
            df.iloc[entry_idx-20:entry_idx]['low']).mean() if entry_idx >= 20 else entry_price * 0.015
        
        stop_loss = entry_price - (direction * atr * 1.5)
        take_profit = entry_price + (direction * atr * 3.0)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # EMA reversal
            if direction == 1 and df.iloc[i]['ema20'] < df.iloc[i]['ema50']:
                return i, bar['close'], 'Trend Reversal'
            if direction == -1 and df.iloc[i]['ema20'] > df.iloc[i]['ema50']:
                return i, bar['close'], 'Trend Reversal'
            
            if i - entry_idx >= 40:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class RangeBreakoutV2(SystematicStrategy):
    """
    Improved Range Breakout - flexible range detection
    """
    
    def __init__(self, name: str = "RangeBreakoutV2"):
        super().__init__(name, direction_bias='BOTH')
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Detect consolidation periods (low volatility)
        df['atr'] = df['high'].sub(df['low']).rolling(14).mean()
        df['atr_slow'] = df['atr'].rolling(50).mean()
        df['low_vol'] = df['atr'] < df['atr_slow'] * 0.7
        
        # Range boundaries
        window = 8  # 2 hours
        df['range_high'] = df['high'].rolling(window).max()
        df['range_low'] = df['low'].rolling(window).min()
        df['range_size'] = df['range_high'] - df['range_low']
        
        # Breakout
        df['break_up'] = (df['close'] > df['range_high'].shift(1)) & \
                        (df['close'] > df['open'])
        df['break_down'] = (df['close'] < df['range_low'].shift(1)) & \
                          (df['close'] < df['open'])
        
        # Volume confirmation
        df['volume_ok'] = df['tick_volume'] > df['tick_volume'].rolling(20).mean() * 0.7
        
        # Session
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = (df['hour'] >= 7) & (df['hour'] < 20)
        
        df['signal'] = 0
        df.loc[df['break_up'] & df['volume_ok'] & df['in_session'], 'signal'] = 1
        df.loc[df['break_down'] & df['volume_ok'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        atr = df.iloc[entry_idx]['atr']
        stop_loss = entry_price - (direction * atr * 1.5)
        take_profit = entry_price + (direction * atr * 2.5)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            if i - entry_idx >= 28:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class MomentumBreakoutV2(SystematicStrategy):
    """
    Momentum-based breakout - works for all symbols
    """
    
    def __init__(self, name: str = "MomentumBreakoutV2"):
        super().__init__(name, direction_bias='BOTH')
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Momentum
        df['roc'] = df['close'].pct_change(10) * 100
        df['roc_ma'] = df['roc'].rolling(20).mean()
        
        # Price vs MA
        df['ma30'] = df['close'].rolling(30).mean()
        df['above_ma'] = df['close'] > df['ma30']
        df['below_ma'] = df['close'] < df['ma30']
        
        # Momentum surge
        df['strong_up'] = (df['roc'] > 0.3) & df['above_ma']
        df['strong_down'] = (df['roc'] < -0.3) & df['below_ma']
        
        # Candle strength
        df['body'] = abs(df['close'] - df['open'])
        df['range'] = df['high'] - df['low']
        df['strong_candle'] = df['body'] > df['range'] * 0.5
        
        # Volume
        df['volume_ok'] = df['tick_volume'] > df['tick_volume'].rolling(20).mean() * 1.2
        
        # Session
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = (df['hour'] >= 7) & (df['hour'] < 20)
        
        df['signal'] = 0
        df.loc[df['strong_up'] & df['strong_candle'] & df['volume_ok'] & df['in_session'], 'signal'] = 1
        df.loc[df['strong_down'] & df['strong_candle'] & df['volume_ok'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        # ATR-based
        atr = df.iloc[entry_idx-20:entry_idx]['high'].sub(
            df.iloc[entry_idx-20:entry_idx]['low']).mean() if entry_idx >= 20 else entry_price * 0.015
        
        stop_loss = entry_price - (direction * atr * 1.8)
        take_profit = entry_price + (direction * atr * 2.5)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Momentum reversal
            if direction == 1 and df.iloc[i]['roc'] < -0.2:
                return i, bar['close'], 'Momentum Fade'
            if direction == -1 and df.iloc[i]['roc'] > 0.2:
                return i, bar['close'], 'Momentum Fade'
            
            if i - entry_idx >= 24:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'
