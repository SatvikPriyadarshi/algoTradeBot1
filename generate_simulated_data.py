import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

def generate_gold_simulated_data(output_path: str, num_candles: int = 5000):
    """
    Generates a realistic simulated Gold (XAUUSD) M1 price feed.
    Models daily volatility, volume clusters, spreads, and clear liquidity sweeps.
    """
    print(f"Generating {num_candles} bars of simulated Gold data...")
    
    np.random.seed(42) # For reproducible sweeps in test
    
    # Base configuration
    start_time = datetime.now() - timedelta(minutes=num_candles)
    times = [start_time + timedelta(minutes=i) for i in range(num_candles)]
    
    # Price parameters
    base_price = 2300.00
    price_drift = 0.01  # Slight drift
    volatility = 0.40   # Volatility multiplier
    
    prices = []
    current_price = base_price
    
    for i in range(num_candles):
        # Model daily volatility cycle (New York session is more volatile)
        hour = times[i].hour
        session_mult = 2.0 if 13 <= hour <= 20 else (1.2 if 7 <= hour <= 12 else 0.5)
        
        change = np.random.normal(price_drift, volatility * session_mult)
        current_price += change
        prices.append(current_price)
        
    df = pd.DataFrame({'time': times, 'close': prices})
    
    # Create Open, High, Low
    df['open'] = df['close'].shift(1).fillna(base_price)
    
    # Generate High/Low relative to Open/Close
    ranges = np.random.exponential(scale=0.8, size=num_candles)
    # Add session volatility to ranges
    for i in range(num_candles):
        hour = times[i].hour
        session_mult = 2.0 if 13 <= hour <= 20 else (1.2 if 7 <= hour <= 12 else 0.5)
        ranges[i] *= session_mult

    df['high'] = df[['open', 'close']].max(axis=1) + (ranges * 0.4)
    df['low'] = df[['open', 'close']].min(axis=1) - (ranges * 0.4)
    
    # Generate volume
    base_volume = 150
    volumes = []
    for i in range(num_candles):
        hour = times[i].hour
        session_mult = 2.5 if 13 <= hour <= 20 else (1.5 if 7 <= hour <= 12 else 0.6)
        
        # Add random spikes representing "news sweeps"
        is_spike = np.random.random() < 0.01
        spike_mult = 12.0 if is_spike else 1.0
        
        vol = int((base_volume + np.random.exponential(scale=100)) * session_mult * spike_mult)
        volumes.append(vol)
        
    df['tick_volume'] = volumes
    df['spread'] = np.random.randint(12, 28, size=num_candles) # Spreads of 1.2 to 2.8 pips/points
    
    # Inject 5 explicit, high-probability liquidity sweeps to ensure the backtester triggers trades!
    # A sweep has a huge wick, large volume, and closing rejection.
    for index in [500, 1200, 2200, 3100, 4200]:
        if index < num_candles:
            # Let's create a swing low first by lowering the price of prior bars
            swing_low_level = df.loc[index - 30, 'low']
            for k in range(index - 29, index):
                df.loc[k, 'low'] = swing_low_level + 2.0
                df.loc[k, 'high'] = swing_low_level + 5.0
                df.loc[k, 'open'] = swing_low_level + 3.0
                df.loc[k, 'close'] = swing_low_level + 3.5
            
            # Now, sweep below that swing low
            df.loc[index, 'open'] = swing_low_level + 3.0
            df.loc[index, 'low'] = swing_low_level - 1.5 # swept by 1.5 points
            df.loc[index, 'close'] = swing_low_level + 2.5 # closed high, showing rejection
            df.loc[index, 'high'] = swing_low_level + 3.5
            df.loc[index, 'tick_volume'] = 3000 # Massive volume spike
            
    # Inject 5 Bearish sweeps
    for index in [800, 1700, 2700, 3800, 4600]:
        if index < num_candles:
            # Create a swing high first
            swing_high_level = df.loc[index - 30, 'high']
            for k in range(index - 29, index):
                df.loc[k, 'high'] = swing_high_level - 2.0
                df.loc[k, 'low'] = swing_high_level - 5.0
                df.loc[k, 'open'] = swing_high_level - 3.0
                df.loc[k, 'close'] = swing_high_level - 3.5
                
            # Sweep above swing high
            df.loc[index, 'open'] = swing_high_level - 3.0
            df.loc[index, 'high'] = swing_high_level + 1.5 # swept high
            df.loc[index, 'close'] = swing_high_level - 2.5 # closed low, rejection
            df.loc[index, 'low'] = swing_high_level - 3.5
            df.loc[index, 'tick_volume'] = 2800 # Volume spike
            
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"Simulated XAUUSD data successfully saved to: {output_path}")

if __name__ == "__main__":
    generate_gold_simulated_data("data/XAUUSD_M1.csv")
