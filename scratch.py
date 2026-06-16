import pandas as pd
import numpy as np
from pathlib import Path
from candidate_strategies import calculate_atr, calculate_rsi

def load_data(symbol: str, timeframe: str = "M15") -> pd.DataFrame:
    df = pd.read_csv(Path("data") / f"{symbol}_{timeframe}.csv")
    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)
    return df

def run_fast_opt():
    df = load_data("EURUSD", "M15")
    # Simulate test split (last 20%)
    split_idx = int(len(df) * 0.8)
    df = df.iloc[split_idx:].copy()
    
    df['atr'] = calculate_atr(df, 14)
    df['rsi'] = calculate_rsi(df, 14)
    
    for fast in [5, 10, 15]:
        for slow in [20, 50, 100]:
            for rsi_thresh in [30, 40, 50]:
                df['ema_fast'] = df['close'].ewm(span=fast).mean()
                df['ema_slow'] = df['close'].ewm(span=slow).mean()
                
                long_cond = (df['ema_fast'] > df['ema_slow']) & (df['rsi'] < rsi_thresh)
                # Filter out consecutive signals
                long_cond = long_cond & (~long_cond.shift(1).fillna(False))
                
                # We only test Longs for simplicity
                signals = df[long_cond].copy()
                if len(signals) < 10:
                    continue
                
                wins = 0
                for idx, row in signals.iterrows():
                    entry_idx = df.index.get_loc(idx)
                    sl = row['close'] - row['atr'] * 1.5
                    tp = row['close'] + row['atr'] * 4.5 # Target A: 1:3
                    
                    max_idx = min(entry_idx + 100, len(df))
                    future = df.iloc[entry_idx+1:max_idx]
                    
                    sl_hits = future[future['low'] <= sl]
                    tp_hits = future[future['high'] >= tp]
                    
                    if len(tp_hits) > 0 and len(sl_hits) > 0:
                        if tp_hits.index[0] < sl_hits.index[0]:
                            wins += 1
                    elif len(tp_hits) > 0:
                        wins += 1
                        
                wr = wins / len(signals)
                total_days = (df.index[-1] - df.index[0]).total_seconds() / 86400
                freq = len(signals) / total_days
                if wr >= 0.45 and freq > 2.0:
                    print(f"Fast:{fast} Slow:{slow} RSI:{rsi_thresh} -> WR: {wr:.1%} Freq: {freq:.2f}")

if __name__ == "__main__":
    run_fast_opt()
