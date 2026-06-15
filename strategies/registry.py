"""Strategy plugin registry."""

from __future__ import annotations

import os

from strategies.base import BaseStrategy
from strategies.opt_mean_rev import OptMeanRevStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {
    OptMeanRevStrategy.name: OptMeanRevStrategy,
}


def list_strategies() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_strategy(name: str | None = None, **kwargs) -> BaseStrategy:
    strategy_name = (name or os.getenv("STRATEGY", OptMeanRevStrategy.name)).strip().lower()
    cls = _REGISTRY.get(strategy_name)
    if cls is None:
        available = ", ".join(list_strategies())
        raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {available}")
    return cls(**kwargs)
