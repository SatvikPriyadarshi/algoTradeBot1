"""
Systematic Trading Strategy Implementations
5+ strategy families with realistic entry/exit logic
"""
import pandas as pd
import numpy as np
from systematic_backtest_engine import SystematicStrategy
from typing import Tuple


class SessionBreakoutStrategy(SystematicStrategy):
    """
    Strategy 1: Session Breakout (London/NY overlap) with ATR filter
    Direction: BOTH (long and short)
    Entry: Breakout of session high/low during active sessions
    Exit: Opposite signal, time-based, or stop/target
    """
    
    def __init__(self, name: str = "SessionBreakout", atr_period: int = 14, 
                 atr_multiplier: float = 1.5, london_start: int = 7, 
                 london_end: int = 16, ny_start: int = 13, ny_end: int = 20):
        super().__init__(name, direction_bias='BOTH')
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.london_start = london_start
        self.london_end = london_end
        self.ny_start = ny_start
        self.ny_end = ny_end
        self.rr_ratio = 2.0
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Calculate ATR
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = df['tr'].rolling(self.atr_period).mean()
        
        # Session identification
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_london'] = (df['hour'] >= self.london_start) & (df['hour'] < self.london_end)
        df['in_ny'] = (df['hour'] >= self.ny_start) & (df['hour'] < self.ny_end)
        df['in_session'] = df['in_london'] | df['in_ny']
        
        # Calculate session high/low (rolling 4-hour window)
        window = 16  # 4 hours in 15-min bars
        df['session_high'] = df['high'].rolling(window, min_periods=1).max()
        df['session_low'] = df['low'].rolling(window, min_periods=1).min()
        
        # Breakout conditions
        df['breakout_up'] = (df['close'] > df['session_high'].shift(1)) & df['in_session']
        df['breakout_down'] = (df['close'] < df['session_low'].shift(1)) & df['in_session']
        
        # ATR filter - only trade when volatility is sufficient
        df['atr_filter'] = df['atr'] > df['atr'].rolling(50).mean() * 0.8
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['breakout_up'] & df['atr_filter'], 'signal'] = 1
        df.loc[df['breakout_down'] & df['atr_filter'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        atr = df.iloc[entry_idx]['atr']
        stop_loss = entry_price - (direction * atr * self.atr_multiplier)
        take_profit = entry_price + (direction * atr * self.atr_multiplier * self.rr_ratio)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss hit
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit hit
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Opposite signal
            if df.iloc[i-1]['signal'] == -direction:
                return i, bar['open'], 'Opposite Signal'
            
            # Time-based exit (max 48 bars = 12 hours)
            if i - entry_idx >= 48:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class MeanReversionVWAPStrategy(SystematicStrategy):
    """
    Strategy 2: Mean Reversion to VWAP
    Direction: BOTH
    Entry: Price deviates from VWAP by N std devs, then reverts
    Exit: Return to VWAP or stop/target
    """
    
    def __init__(self, name: str = "MeanRevVWAP", vwap_period: int = 20,
                 std_entry: float = 1.5, std_stop: float = 2.5):
        super().__init__(name, direction_bias='BOTH')
        self.vwap_period = vwap_period
        self.std_entry = std_entry
        self.std_stop = std_stop
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Calculate VWAP
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['volume'] = df['tick_volume']
        df['vwap'] = (df['typical_price'] * df['volume']).rolling(self.vwap_period).sum() / \
                     df['volume'].rolling(self.vwap_period).sum()
        
        # Standard deviation bands
        df['price_std'] = df['close'].rolling(self.vwap_period).std()
        df['upper_band'] = df['vwap'] + (df['price_std'] * self.std_entry)
        df['lower_band'] = df['vwap'] - (df['price_std'] * self.std_entry)
        
        # Session filter (London/NY)
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = ((df['hour'] >= 7) & (df['hour'] < 20))
        
        # Mean reversion conditions
        df['oversold'] = (df['close'] < df['lower_band']) & (df['close'].shift(1) >= df['lower_band'].shift(1))
        df['overbought'] = (df['close'] > df['upper_band']) & (df['close'].shift(1) <= df['upper_band'].shift(1))
        
        # Reversion confirmation (price moving back toward VWAP)
        df['reverting_up'] = df['oversold'] & (df['close'] > df['open'])
        df['reverting_down'] = df['overbought'] & (df['close'] < df['open'])
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['reverting_up'] & df['in_session'], 'signal'] = 1
        df.loc[df['reverting_down'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        entry_bar = df.iloc[entry_idx]
        vwap = entry_bar['vwap']
        std = entry_bar['price_std']
        
        stop_loss = entry_price - (direction * std * self.std_stop)
        take_profit = vwap  # Target is VWAP
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit (reached VWAP)
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Time-based exit (max 32 bars = 8 hours)
            if i - entry_idx >= 32:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class TrendPullbackStrategy(SystematicStrategy):
    """
    Strategy 3: Trend Pullback (EMA stack + RSI)
    Direction: BOTH
    Entry: Strong trend + pullback to support/resistance
    Exit: Continuation or reversal
    """
    
    def __init__(self, name: str = "TrendPullback", ema_fast: int = 9, 
                 ema_mid: int = 21, ema_slow: int = 50, rsi_period: int = 14,
                 rsi_oversold: float = 35, rsi_overbought: float = 65):
        super().__init__(name, direction_bias='BOTH')
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # EMAs
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_mid'] = df['close'].ewm(span=self.ema_mid, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Trend identification (EMA stack)
        df['uptrend'] = (df['ema_fast'] > df['ema_mid']) & (df['ema_mid'] > df['ema_slow'])
        df['downtrend'] = (df['ema_fast'] < df['ema_mid']) & (df['ema_mid'] < df['ema_slow'])
        
        # Pullback conditions
        df['pullback_long'] = df['uptrend'] & (df['rsi'] < self.rsi_oversold) & \
                             (df['close'] < df['ema_fast']) & (df['close'] > df['ema_mid'])
        df['pullback_short'] = df['downtrend'] & (df['rsi'] > self.rsi_overbought) & \
                              (df['close'] > df['ema_fast']) & (df['close'] < df['ema_mid'])
        
        # Confirmation: price starting to move back in trend direction
        df['bounce_up'] = (df['close'] > df['open']) & (df['close'].shift(1) < df['open'].shift(1))
        df['bounce_down'] = (df['close'] < df['open']) & (df['close'].shift(1) > df['open'].shift(1))
        
        # Session filter
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = ((df['hour'] >= 7) & (df['hour'] < 20))
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['pullback_long'] & df['bounce_up'] & df['in_session'], 'signal'] = 1
        df.loc[df['pullback_short'] & df['bounce_down'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        # ATR-based stop and target
        atr = df.iloc[entry_idx-20:entry_idx]['high'].sub(
            df.iloc[entry_idx-20:entry_idx]['low']).mean() if entry_idx >= 20 else entry_price * 0.015
        
        stop_loss = entry_price - (direction * atr * 2.0)
        take_profit = entry_price + (direction * atr * 3.0)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Trend reversal (EMA cross)
            if direction == 1 and df.iloc[i]['ema_fast'] < df.iloc[i]['ema_mid']:
                return i, bar['close'], 'Trend Reversal'
            if direction == -1 and df.iloc[i]['ema_fast'] > df.iloc[i]['ema_mid']:
                return i, bar['close'], 'Trend Reversal'
            
            # Max holding period (40 bars = 10 hours)
            if i - entry_idx >= 40:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class VolatilityBreakoutStrategy(SystematicStrategy):
    """
    Strategy 4: Volatility Compression Breakout (Bollinger/Keltner Squeeze)
    Direction: BOTH
    Entry: Low volatility squeeze followed by breakout
    Exit: Volatility expansion complete or reversal
    """
    
    def __init__(self, name: str = "VolBreakout", bb_period: int = 20, 
                 bb_std: float = 2.0, kc_period: int = 20, kc_atr_mult: float = 1.5):
        super().__init__(name, direction_bias='BOTH')
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_atr_mult = kc_atr_mult
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Bollinger Bands
        df['bb_mid'] = df['close'].rolling(self.bb_period).mean()
        df['bb_std'] = df['close'].rolling(self.bb_period).std()
        df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * self.bb_std)
        df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * self.bb_std)
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
        
        # Keltner Channels
        df['kc_mid'] = df['close'].ewm(span=self.kc_period, adjust=False).mean()
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = df['tr'].rolling(self.kc_period).mean()
        df['kc_upper'] = df['kc_mid'] + (df['atr'] * self.kc_atr_mult)
        df['kc_lower'] = df['kc_mid'] - (df['atr'] * self.kc_atr_mult)
        
        # Squeeze: BB inside KC
        df['squeeze'] = (df['bb_lower'] > df['kc_lower']) & (df['bb_upper'] < df['kc_upper'])
        df['squeeze_off'] = df['squeeze'].shift(1) & ~df['squeeze']
        
        # Low volatility confirmation
        df['low_vol'] = df['bb_width'] < df['bb_width'].rolling(50).quantile(0.25)
        
        # Breakout direction
        df['breakout_up'] = df['squeeze_off'] & (df['close'] > df['bb_mid']) & \
                           (df['close'] > df['close'].shift(1))
        df['breakout_down'] = df['squeeze_off'] & (df['close'] < df['bb_mid']) & \
                             (df['close'] < df['close'].shift(1))
        
        # Session filter
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_session'] = ((df['hour'] >= 7) & (df['hour'] < 20))
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['breakout_up'] & df['in_session'], 'signal'] = 1
        df.loc[df['breakout_down'] & df['in_session'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        atr = df.iloc[entry_idx]['atr']
        stop_loss = entry_price - (direction * atr * 2.0)
        take_profit = entry_price + (direction * atr * 3.0)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Squeeze re-forms (volatility collapses again)
            if df.iloc[i]['squeeze']:
                return i, bar['close'], 'Squeeze Reformed'
            
            # Max holding (36 bars = 9 hours)
            if i - entry_idx >= 36:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class LondonOpenRangeStrategy(SystematicStrategy):
    """
    Strategy 5: London Open Range Breakout
    Direction: BOTH
    Entry: Breakout of first 30-60 min range during London session
    Exit: End of session or stop/target
    """
    
    def __init__(self, name: str = "LondonORB", range_minutes: int = 60,
                 london_open_hour: int = 7, range_mult: float = 1.2):
        super().__init__(name, direction_bias='BOTH')
        self.range_bars = range_minutes // 15  # Convert to 15-min bars
        self.london_open_hour = london_open_hour
        self.range_mult = range_mult
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['minute'] = pd.to_datetime(df['time']).dt.minute
        df['date'] = pd.to_datetime(df['time']).dt.date
        
        # Identify London open (7:00 UTC)
        df['london_open'] = (df['hour'] == self.london_open_hour) & (df['minute'] == 0)
        
        # Calculate opening range high/low for each day
        df['range_high'] = np.nan
        df['range_low'] = np.nan
        df['range_size'] = np.nan
        
        for date in df['date'].unique():
            day_mask = df['date'] == date
            day_data = df[day_mask].copy()
            
            # Find London open bar
            london_open_mask = day_data['london_open']
            if london_open_mask.sum() > 0:
                open_idx = day_data[london_open_mask].index[0]
                range_end_idx = min(open_idx + self.range_bars, df.index.max())
                
                # Calculate range
                range_bars = df.loc[open_idx:range_end_idx]
                range_high = range_bars['high'].max()
                range_low = range_bars['low'].min()
                range_size = range_high - range_low
                
                # Apply to all bars after range formation
                df.loc[range_end_idx+1:, 'range_high'] = range_high
                df.loc[range_end_idx+1:, 'range_low'] = range_low
                df.loc[range_end_idx+1:, 'range_size'] = range_size
        
        df['range_high'] = df['range_high'].ffill()
        df['range_low'] = df['range_low'].ffill()
        df['range_size'] = df['range_size'].ffill()
        
        # Breakout conditions
        df['breakout_up'] = (df['close'] > df['range_high']) & \
                           (df['close'].shift(1) <= df['range_high'].shift(1)) & \
                           (df['hour'] >= self.london_open_hour) & (df['hour'] < 16)
        df['breakout_down'] = (df['close'] < df['range_low']) & \
                             (df['close'].shift(1) >= df['range_low'].shift(1)) & \
                             (df['hour'] >= self.london_open_hour) & (df['hour'] < 16)
        
        # Volume filter
        df['volume'] = df['tick_volume']
        df['avg_volume'] = df['volume'].rolling(20).mean()
        df['vol_ok'] = df['volume'] > df['avg_volume'] * 0.8
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['breakout_up'] & df['vol_ok'], 'signal'] = 1
        df.loc[df['breakout_down'] & df['vol_ok'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        range_size = df.iloc[entry_idx]['range_size']
        stop_loss = entry_price - (direction * range_size * 0.5)
        take_profit = entry_price + (direction * range_size * self.range_mult)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # End of London session (16:00)
            if df.iloc[i]['hour'] >= 16:
                return i, bar['close'], 'Session Close'
            
            # Max holding (24 bars = 6 hours)
            if i - entry_idx >= 24:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'


class GoldMomentumStrategy(SystematicStrategy):
    """
    Strategy 6: Gold-Specific NY Session Momentum
    Direction: BOTH
    Entry: Strong momentum during NY session (13:00-20:00 UTC)
    Exit: Momentum fades or session ends
    """
    
    def __init__(self, name: str = "GoldNYMomentum", momentum_period: int = 10,
                 volume_mult: float = 1.5, ny_start: int = 13, ny_end: int = 20):
        super().__init__(name, direction_bias='BOTH')
        self.momentum_period = momentum_period
        self.volume_mult = volume_mult
        self.ny_start = ny_start
        self.ny_end = ny_end
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # NY session filter
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['in_ny'] = (df['hour'] >= self.ny_start) & (df['hour'] < self.ny_end)
        
        # Momentum indicators
        df['roc'] = df['close'].pct_change(self.momentum_period) * 100
        df['volume'] = df['tick_volume']
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['high_volume'] = df['volume'] > df['volume_ma'] * self.volume_mult
        
        # Price momentum
        df['price_ma'] = df['close'].rolling(20).mean()
        df['momentum_up'] = (df['close'] > df['price_ma']) & (df['roc'] > 0.5)
        df['momentum_down'] = (df['close'] < df['price_ma']) & (df['roc'] < -0.5)
        
        # Candle strength
        df['body'] = abs(df['close'] - df['open'])
        df['range'] = df['high'] - df['low']
        df['strong_candle'] = df['body'] > df['range'] * 0.6
        
        # ATR for volatility
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = df['tr'].rolling(14).mean()
        df['atr_high'] = df['atr'] > df['atr'].rolling(50).mean()
        
        # Generate signals
        df['signal'] = 0
        df.loc[df['momentum_up'] & df['high_volume'] & df['strong_candle'] & 
               df['in_ny'] & df['atr_high'], 'signal'] = 1
        df.loc[df['momentum_down'] & df['high_volume'] & df['strong_candle'] & 
               df['in_ny'] & df['atr_high'], 'signal'] = -1
        
        return df
    
    def calculate_exit(self, df: pd.DataFrame, entry_idx: int, 
                      direction: int, entry_price: float) -> Tuple[int, float, str]:
        atr = df.iloc[entry_idx]['atr']
        stop_loss = entry_price - (direction * atr * 1.5)
        take_profit = entry_price + (direction * atr * 2.5)
        
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            
            # Stop loss
            if direction == 1 and bar['low'] <= stop_loss:
                return i, stop_loss, 'Stop Loss'
            if direction == -1 and bar['high'] >= stop_loss:
                return i, stop_loss, 'Stop Loss'
            
            # Take profit
            if direction == 1 and bar['high'] >= take_profit:
                return i, take_profit, 'Take Profit'
            if direction == -1 and bar['low'] <= take_profit:
                return i, take_profit, 'Take Profit'
            
            # Momentum reversal
            if direction == 1 and df.iloc[i]['roc'] < 0:
                return i, bar['close'], 'Momentum Reversal'
            if direction == -1 and df.iloc[i]['roc'] > 0:
                return i, bar['close'], 'Momentum Reversal'
            
            # Session end
            if df.iloc[i]['hour'] >= self.ny_end:
                return i, bar['close'], 'Session End'
            
            # Max holding (20 bars = 5 hours)
            if i - entry_idx >= 20:
                return i, bar['close'], 'Time Exit'
        
        return len(df) - 1, df.iloc[-1]['close'], 'End of Data'
