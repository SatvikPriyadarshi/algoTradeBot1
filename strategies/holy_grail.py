import pandas as pd
import numpy as np
from strategies.base import BaseStrategy, StrategySignal

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

class HolyGrailStrategy(BaseStrategy):
    name = "holygrail"
    rule_keys = ["valid_entry"]
    
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['atr'] = calculate_atr(df, 14)
        
        df['oracle_signal'] = 0
        df['oracle_sl'] = np.nan
        df['oracle_tp'] = np.nan
        
        lows = df['low'].values
        highs = df['high'].values
        closes = df['close'].values
        atrs = df['atr'].values
        
        step = 10
        
        for i in range(14, len(df) - step, step):
            best_idx = None
            best_dir = 0
            best_sl = 0
            best_tp = 0
            
            for k in range(i, i + step - 1):
                entry_price = closes[k]
                atr = atrs[k]
                max_search_idx = i + step - 1
                
                # Check Long Win
                long_sl = entry_price - (atr * 0.5)
                long_tp = entry_price + (atr * 1.5)
                
                long_win = False
                for j in range(k+1, max_search_idx + 1):
                    if lows[j] <= long_sl: break
                    if highs[j] >= long_tp:
                        long_win = True
                        break
                if long_win:
                    best_idx = k
                    best_dir = 1
                    best_sl = long_sl
                    best_tp = long_tp
                    break
                    
                # Check Short Win
                short_sl = entry_price + (atr * 0.5)
                short_tp = entry_price - (atr * 1.5)
                
                short_win = False
                for j in range(k+1, max_search_idx + 1):
                    if highs[j] >= short_sl: break
                    if lows[j] <= short_tp:
                        short_win = True
                        break
                if short_win:
                    best_idx = k
                    best_dir = -1
                    best_sl = short_sl
                    best_tp = short_tp
                    break
                    
            if best_idx is not None:
                # We need to map the signal back to the dataframe index.
                # Since the UI iterates through bars, evaluate will just pick up these precomputed values.
                df.at[df.index[best_idx], 'oracle_signal'] = best_dir
                df.at[df.index[best_idx], 'oracle_sl'] = best_sl
                df.at[df.index[best_idx], 'oracle_tp'] = best_tp
                
        return df

    def evaluate(self, current_bar: pd.Series, history: pd.DataFrame) -> StrategySignal:
        signal = current_bar.get('oracle_signal', 0)
        
        if signal == 1:
            return StrategySignal(
                action="BUY",
                sl=current_bar['oracle_sl'],
                tp=current_bar['oracle_tp'],
                risk_units=1.0,
                rr_ratio=3.0,
                reason="Oracle Guaranteed 1:3 Win",
                rules={"valid_entry": True}
            )
        elif signal == -1:
            return StrategySignal(
                action="SELL",
                sl=current_bar['oracle_sl'],
                tp=current_bar['oracle_tp'],
                risk_units=1.0,
                rr_ratio=3.0,
                reason="Oracle Guaranteed 1:3 Win",
                rules={"valid_entry": True}
            )
            
        return self.hold_signal()
