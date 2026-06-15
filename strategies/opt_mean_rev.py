"""
OptimizedMeanReversion — M15 mean reversion (FX majors + XAUUSD).

Entry/exit logic matches run_final_optimized.py / systematic_backtest_engine.
"""

from __future__ import annotations

import os
from typing import Tuple

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, StrategySignal

MA_PERIOD = 25
STD_BAND_MULT = 2.0
STOP_STD_MULT = 2.2
MAX_HOLD_BARS = 20
MEAN_LONG_BUFFER = 0.998
MEAN_SHORT_BUFFER = 1.002


def add_mean_reversion_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Same indicator pipeline as OptimizedMeanReversion.generate_signals."""
    df = df.copy()
    df["ma25"] = df["close"].rolling(MA_PERIOD).mean()
    df["std"] = df["close"].rolling(MA_PERIOD).std()
    df["upper"] = df["ma25"] + (df["std"] * STD_BAND_MULT)
    df["lower"] = df["ma25"] - (df["std"] * STD_BAND_MULT)

    df["atr"] = df["high"].sub(df["low"]).rolling(14).mean()
    df["atr_ok"] = (df["atr"] > df["atr"].rolling(100).quantile(0.2)) & (
        df["atr"] < df["atr"].rolling(100).quantile(0.85)
    )

    df["oversold"] = (df["close"] < df["lower"]) & (df["close"].shift(1) >= df["lower"].shift(1))
    df["overbought"] = (df["close"] > df["upper"]) & (df["close"].shift(1) <= df["upper"].shift(1))
    df["signal"] = 0
    df.loc[df["oversold"] & df["atr_ok"], "signal"] = 1
    df.loc[df["overbought"] & df["atr_ok"], "signal"] = -1
    return df


def build_trade_levels(
    direction: int, entry_price: float, ma: float, std: float
) -> tuple[float, float, float] | None:
    """Stop at 2.2×STD; mean target with trigger buffer. Returns (sl, tp, tp_trigger) or None."""
    if direction == 1:
        sl = entry_price - (std * STOP_STD_MULT)
        tp = ma
        tp_trigger = ma * MEAN_LONG_BUFFER
        if ma <= entry_price:
            return None
    else:
        sl = entry_price + (std * STOP_STD_MULT)
        tp = ma
        tp_trigger = ma * MEAN_SHORT_BUFFER
        if ma >= entry_price:
            return None
    return sl, tp, tp_trigger


def simulate_mean_reversion_exit(
    df: pd.DataFrame, entry_idx: int, direction: int, entry_price: float
) -> Tuple[int, float, str]:
    """Exact exit simulation from OptimizedMeanReversion.calculate_exit."""
    entry_bar = df.iloc[entry_idx]
    ma = float(entry_bar["ma25"])
    std = float(entry_bar["std"])
    stop_loss = entry_price - (direction * std * STOP_STD_MULT)
    take_profit = ma

    for i in range(entry_idx + 1, len(df)):
        bar = df.iloc[i]

        if direction == 1 and bar["low"] <= stop_loss:
            return i, float(stop_loss), "Stop Loss"
        if direction == -1 and bar["high"] >= stop_loss:
            return i, float(stop_loss), "Stop Loss"

        if direction == 1 and bar["high"] >= take_profit * MEAN_LONG_BUFFER:
            exit_price = min(float(bar["high"]), take_profit)
            return i, exit_price, "Mean Reached"
        if direction == -1 and bar["low"] <= take_profit * MEAN_SHORT_BUFFER:
            exit_price = max(float(bar["low"]), take_profit)
            return i, exit_price, "Mean Reached"

        if i - entry_idx >= MAX_HOLD_BARS:
            return i, float(bar["close"]), "Time Exit"

    return len(df) - 1, float(df.iloc[-1]["close"]), "End of Data"


class OptMeanRevStrategy(BaseStrategy):
    name = "opt_mean_rev"
    entry_at_open = True
    rule_keys = ["atr_ok", "band_signal", "stops_set"]

    def __init__(self):
        self.max_hold_bars = int(os.getenv("OPT_MAX_HOLD_BARS", MAX_HOLD_BARS))

    def lookback_bars(self) -> int:
        return 120

    def warmup_bars(self) -> int:
        return 105

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy().sort_values("time").reset_index(drop=True)
        df = add_mean_reversion_indicators(df)
        return df

    def evaluate(self, current_bar: pd.Series, history: pd.DataFrame) -> StrategySignal:
        rules = self.default_rules()
        if history is None or len(history) < 1:
            return self.hold_signal(rules)

        prev = history.iloc[-1]
        signal_dir = int(prev.get("signal", 0) or 0)
        rules["atr_ok"] = bool(prev.get("atr_ok", False))
        rules["band_signal"] = signal_dir != 0

        if signal_dir == 0:
            return self.hold_signal(rules)

        ma = float(current_bar.get("ma25", np.nan))
        std = float(current_bar.get("std", np.nan))
        if pd.isna(ma) or pd.isna(std) or std <= 0:
            return self.hold_signal(rules)

        entry_price = float(current_bar["open"])
        action = "BUY" if signal_dir == 1 else "SELL"
        levels = build_trade_levels(signal_dir, entry_price, ma, std)
        if levels is None:
            return self.hold_signal(rules)
        sl, tp, tp_trigger = levels
        rules["stops_set"] = True

        tag = "Mean Rev Long" if signal_dir == 1 else "Mean Rev Short"
        risk = abs(entry_price - sl)
        rr = abs(tp - entry_price) / risk if risk > 0 else 0.0

        return StrategySignal(
            action=action,
            sl=sl,
            tp=tp,
            trigger_level=ma,
            risk_units=risk,
            rr_ratio=rr,
            rules=rules,
            meta={
                "ma25": ma,
                "std": std,
                "upper": float(prev.get("upper", 0) or 0),
                "lower": float(prev.get("lower", 0) or 0),
                "tp_trigger": tp_trigger,
                "max_hold_bars": self.max_hold_bars,
                "entry_on_next_open": True,
            },
            reason=f"{tag} | MA25 {ma:.5f} | SL {STOP_STD_MULT} std | max {self.max_hold_bars} bars",
        )


# ── SystematicStrategy adapter (run_final_optimized.py) ─────────────────────

try:
    from systematic_backtest_engine import SystematicStrategy

    class OptimizedMeanReversion(SystematicStrategy):
        """Drop-in replacement — same class used by run_final_optimized.py."""

        def __init__(self, name: str = "OptMeanRev"):
            super().__init__(name, direction_bias="BOTH")

        def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
            return add_mean_reversion_indicators(df)

        def calculate_exit(
            self, df: pd.DataFrame, entry_idx: int, direction: int, entry_price: float
        ) -> Tuple[int, float, str]:
            return simulate_mean_reversion_exit(df, entry_idx, direction, entry_price)

except ImportError:
    OptimizedMeanReversion = None  # type: ignore
