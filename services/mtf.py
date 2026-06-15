"""Multi-timeframe helpers — resample LTF bars to HTF without look-ahead."""

from __future__ import annotations

import numpy as np
import pandas as pd


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = df.copy().set_index("time")
    agg = work.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"}
    )
    return agg.dropna(subset=["open"]).reset_index()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


def attach_htf_columns(
    ltf_df: pd.DataFrame,
    htf_rule: str = "1h",
    ema_fast: int = 9,
    ema_slow: int = 21,
    rsi_period: int = 14,
    prefix: str = "htf",
    range_lookback: int = 20,
) -> pd.DataFrame:
    """Merge completed HTF values onto LTF bars (backward asof — no look-ahead)."""
    ltf = ltf_df.copy().sort_values("time").reset_index(drop=True)
    htf = resample_ohlcv(ltf, htf_rule)

    p = prefix
    htf[f"{p}_ema_fast"] = ema(htf["close"], ema_fast)
    htf[f"{p}_ema_slow"] = ema(htf["close"], ema_slow)
    htf[f"{p}_rsi"] = rsi_series(htf["close"], rsi_period)
    htf[f"{p}_atr"] = atr_series(htf, 14)
    htf[f"{p}_adx"] = adx_series(htf, 14)
    htf[f"{p}_prev_high"] = htf["high"].shift(1)
    htf[f"{p}_prev_low"] = htf["low"].shift(1)
    htf[f"{p}_range_high"] = htf["high"].rolling(range_lookback, min_periods=5).max().shift(1)
    htf[f"{p}_range_low"] = htf["low"].rolling(range_lookback, min_periods=5).min().shift(1)
    rh = htf[f"{p}_range_high"]
    rl = htf[f"{p}_range_low"]
    htf[f"{p}_equilibrium"] = (rh + rl) / 2.0

    bullish = (htf["close"] > htf[f"{p}_ema_slow"]) & (htf[f"{p}_ema_fast"] > htf[f"{p}_ema_slow"])
    bearish = (htf["close"] < htf[f"{p}_ema_slow"]) & (htf[f"{p}_ema_fast"] < htf[f"{p}_ema_slow"])
    htf[f"{p}_bias"] = np.where(bullish, 1, np.where(bearish, -1, 0))

    merge_cols = [
        "time",
        f"{p}_bias",
        f"{p}_ema_fast",
        f"{p}_ema_slow",
        f"{p}_rsi",
        f"{p}_atr",
        f"{p}_adx",
        f"{p}_prev_high",
        f"{p}_prev_low",
        f"{p}_range_high",
        f"{p}_range_low",
        f"{p}_equilibrium",
        "close",
    ]
    htf_merge = htf[merge_cols].rename(columns={"close": f"{p}_close", "time": f"{p}_time"})

    return pd.merge_asof(
        ltf,
        htf_merge,
        left_on="time",
        right_on=f"{p}_time",
        direction="backward",
    )


def attach_daily_levels(ltf_df: pd.DataFrame) -> pd.DataFrame:
    ltf = ltf_df.copy().sort_values("time").reset_index(drop=True)
    daily = resample_ohlcv(ltf, "1D")
    daily["pdh"] = daily["high"].shift(1)
    daily["pdl"] = daily["low"].shift(1)
    daily["daily_ema"] = ema(daily["close"], 20)
    daily["daily_bias"] = np.where(daily["close"] > daily["daily_ema"], 1, np.where(daily["close"] < daily["daily_ema"], -1, 0))
    dmerge = daily[["time", "pdh", "pdl", "daily_bias"]].rename(columns={"time": "day_time"})
    return pd.merge_asof(ltf, dmerge, left_on="time", right_on="day_time", direction="backward")
