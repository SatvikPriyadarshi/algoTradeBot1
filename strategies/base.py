"""Strategy plugin interface — swap strategies without touching engine/backtester."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class StrategySignal:
    action: str = "HOLD"
    sl: float = 0.0
    tp: float = 0.0
    trigger_level: float = 0.0
    risk_units: float = 0.0
    rr_ratio: float = 0.0
    reason: str = ""
    rules: dict[str, bool] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "sl": self.sl,
            "tp": self.tp,
            "trigger_level": self.trigger_level,
            "risk_units": self.risk_units,
            "rr_ratio": self.rr_ratio,
            "reason": self.reason,
            "rules": self.rules,
            "meta": self.meta,
        }


class BaseStrategy(ABC):
    """Every strategy plugin must implement this contract."""

    name: str = "base"
    rule_keys: list[str] = []

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pre-compute indicators/levels on the full dataframe (no look-ahead)."""

    @abstractmethod
    def evaluate(self, current_bar: pd.Series, history: pd.DataFrame) -> StrategySignal:
        """Return a trade signal for the current bar using only past data in `history`."""

    def default_rules(self) -> dict[str, bool]:
        return {key: False for key in self.rule_keys}

    def warmup_bars(self) -> int:
        """Minimum bars required before `evaluate` is meaningful."""
        return 50

    def lookback_bars(self) -> int:
        """History window passed to `evaluate` each bar."""
        return getattr(self, "profile_lookback", 120)

    def hold_signal(self, rules: dict[str, bool] | None = None) -> StrategySignal:
        merged = self.default_rules()
        if rules:
            merged.update(rules)
        return StrategySignal(action="HOLD", rules=merged)


def validate_rr(entry: float, sl: float, tp: float, target_ratio: float = 3.0, tol: float = 0.05) -> tuple[bool, float]:
    """Return (is_valid, actual_ratio) for a planned trade."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return False, 0.0
    actual = reward / risk
    return abs(actual - target_ratio) <= tol, actual


def build_rr_trade(
    action: str,
    entry: float,
    sl: float,
    target_ratio: float = 3.0,
) -> tuple[float, float, float, float, bool]:
    """
    Build stop and take-profit for a strict target R:R.
    Returns (sl, tp, risk_units, rr_ratio, is_valid).
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return sl, entry, 0.0, 0.0, False

    if action == "BUY":
        tp = entry + (risk * target_ratio)
    elif action == "SELL":
        tp = entry - (risk * target_ratio)
    else:
        return sl, entry, 0.0, 0.0, False

    valid, actual_rr = validate_rr(entry, sl, tp, target_ratio)
    return sl, tp, risk, actual_rr, valid
