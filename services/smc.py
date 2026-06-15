"""SMC-style structures: FVG, supply/demand zones on OHLCV."""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.mtf import atr_series, resample_ohlcv


def find_active_fvg(
    df: pd.DataFrame,
    direction: int,
    lookback: int = 40,
    max_age: int = 25,
) -> dict | None:
    """Nearest unmitigated Fair Value Gap in trend direction."""
    if df is None or len(df) < 5:
        return None

    work = df.iloc[-lookback:].reset_index(drop=True)
    best = None

    for i in range(2, len(work)):
        age = len(work) - 1 - i
        if age > max_age:
            continue

        if direction == 1:
            gap_low = float(work.iloc[i - 2]["high"])
            gap_high = float(work.iloc[i]["low"])
            if gap_high <= gap_low:
                continue
            mitigated = any(float(work.iloc[j]["low"]) <= gap_high for j in range(i + 1, len(work)))
            if mitigated:
                continue
            zone = {"low": gap_low, "high": gap_high, "mid": (gap_low + gap_high) / 2, "age": age}
            if best is None or age < best["age"]:
                best = zone
        else:
            gap_high = float(work.iloc[i - 2]["low"])
            gap_low = float(work.iloc[i]["high"])
            if gap_low >= gap_high:
                continue
            mitigated = any(float(work.iloc[j]["high"]) >= gap_low for j in range(i + 1, len(work)))
            if mitigated:
                continue
            zone = {"low": gap_low, "high": gap_high, "mid": (gap_low + gap_high) / 2, "age": age}
            if best is None or age < best["age"]:
                best = zone

    return best


def build_h1_supply_demand_zones(ltf_df: pd.DataFrame, impulse_atr_mult: float = 1.2) -> pd.DataFrame:
    """H1 demand/supply bases merged onto LTF bars."""
    ltf = ltf_df.copy().sort_values("time").reset_index(drop=True)
    h1 = resample_ohlcv(ltf, "1h")
    if len(h1) < 10:
        for col in ("h1_demand_low", "h1_demand_high", "h1_supply_low", "h1_supply_high"):
            ltf[col] = np.nan
        return ltf

    h1["atr"] = atr_series(h1, 14)
    h1["body"] = (h1["close"] - h1["open"]).abs()
    h1["bull_impulse"] = (h1["close"] > h1["open"]) & (h1["body"] >= h1["atr"] * impulse_atr_mult)
    h1["bear_impulse"] = (h1["close"] < h1["open"]) & (h1["body"] >= h1["atr"] * impulse_atr_mult)

    demand_low = np.full(len(h1), np.nan)
    demand_high = np.full(len(h1), np.nan)
    supply_low = np.full(len(h1), np.nan)
    supply_high = np.full(len(h1), np.nan)

    for i in range(4, len(h1)):
        if h1.iloc[i]["bull_impulse"]:
            base = h1.iloc[max(0, i - 4) : i]
            demand_low[i] = float(base["low"].min())
            demand_high[i] = float(base["high"].max())
        if h1.iloc[i]["bear_impulse"]:
            base = h1.iloc[max(0, i - 4) : i]
            supply_low[i] = float(base["low"].min())
            supply_high[i] = float(base["high"].max())

    h1["h1_demand_low"] = pd.Series(demand_low).ffill()
    h1["h1_demand_high"] = pd.Series(demand_high).ffill()
    h1["h1_supply_low"] = pd.Series(supply_low).ffill()
    h1["h1_supply_high"] = pd.Series(supply_high).ffill()

    merge = h1[["time", "h1_demand_low", "h1_demand_high", "h1_supply_low", "h1_supply_high"]].rename(
        columns={"time": "h1_zone_time"}
    )
    return pd.merge_asof(ltf, merge, left_on="time", right_on="h1_zone_time", direction="backward")
