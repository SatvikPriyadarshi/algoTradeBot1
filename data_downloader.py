import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import os
import argparse
import sys

def download_mt5_data(symbol: str, timeframe_str: str, num_bars: int, output_path: str):
    """
    Downloads historical bar data from MetaTrader 5 and saves it to a CSV.
    """
    print(f"Initializing connection to MetaTrader 5...")
    if not mt5.initialize():
        print(f"MetaTrader 5 initialization failed. Error code: {mt5.last_error()}", file=sys.stderr)
        print("Make sure the MT5 terminal is open on your system.", file=sys.stderr)
        return False
        
    print("MT5 connection established successfully.")
    
    # Map timeframe string to MT5 constants
    timeframes = {
        'M1': mt5.TIMEFRAME_M1,
        'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15,
        'M30': mt5.TIMEFRAME_M30,
        'H1': mt5.TIMEFRAME_H1,
        'H2': mt5.TIMEFRAME_H2,
        'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1
    }
    
    if timeframe_str not in timeframes:
        print(f"Invalid timeframe selection. Supported: {list(timeframes.keys())}", file=sys.stderr)
        mt5.shutdown()
        return False
        
    tf = timeframes[timeframe_str]
    
    # Check if symbol is available in MT5
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"Symbol '{symbol}' not found in MT5. Checking market watch list...", file=sys.stderr)
        # Try to select the symbol
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol '{symbol}'. Error: {mt5.last_error()}", file=sys.stderr)
            mt5.shutdown()
            return False
        symbol_info = mt5.symbol_info(symbol)
        
    print(f"Downloading {num_bars} bars for {symbol} ({timeframe_str})...")
    
    # Fetch rates
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, num_bars)
    
    mt5.shutdown()
    print("MT5 connection closed.")
    
    if rates is None or len(rates) == 0:
        print("No historical data returned from MT5. Ensure you have historical charts downloaded.", file=sys.stderr)
        return False
        
    # Convert to DataFrame
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Keep only necessary columns for the backtester
    # Column mapping: time, open, high, low, close, tick_volume, spread, real_volume
    df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread']]
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    # Save to CSV
    df.to_csv(output_path, index=False)
    print(f"Data saved successfully to {output_path} (Total rows: {len(df)})")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download historical data from MT5 to CSV")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol ticker (e.g. XAUUSD, EURUSD)")
    parser.add_argument("--timeframe", type=str, default="M1", help="Timeframe (M1, M5, M15, H1, H4, D1)")
    parser.add_argument("--bars", type=int, default=50000, help="Number of historical candles to fetch")
    parser.add_argument("--output", type=str, default="data/XAUUSD_M1.csv", help="Output CSV file path")
    
    args = parser.parse_args()
    
    success = download_mt5_data(args.symbol, args.timeframe, args.bars, args.output)
    sys.exit(0 if success else 1)
