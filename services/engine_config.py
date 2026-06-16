"""Engine settings from .env — shared by live, backtest, and UI."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def get_symbols() -> list[str]:
    raw = os.getenv("SYMBOLS", "GBPUSD,EURUSD,XAUUSD")
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def get_engine_mode() -> str:
    mode = os.getenv("ENGINE_MODE", "dry_run").strip().lower()
    return mode if mode in ("live", "dry_run") else "dry_run"


def get_mt5_credentials() -> dict | None:
    """MT5 login from .env — engine connects on startup (no dashboard needed)."""
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "").strip()
    if not login_raw or not password or not server:
        return None
    try:
        login = int(login_raw)
    except ValueError:
        return None
    return {"login": login, "password": password, "server": server}


def get_risk_settings() -> dict:
    return {
        "strategy": os.getenv("STRATEGY", "opt_mean_rev").strip().lower(),
        "timeframe": os.getenv("ENGINE_TIMEFRAME", "M15").strip().upper(),
        "engine_mode": get_engine_mode(),
        "engine_port": _int("ENGINE_PORT", 5076),
        "mt5_login": os.getenv("MT5_LOGIN", "").strip(),
        "mt5_server": os.getenv("MT5_SERVER", "").strip(),
        "mt5_auto_login": get_mt5_credentials() is not None,
        "risk_per_trade": _float("RISK_PER_TRADE", 50.0),
        # Backtest values are explicitly passed via UI payload; defaults if missing:
        "backtest_trade_capital": 500.0,
        "backtest_leverage": 100.0,
        "max_daily_loss": _float("MAX_DAILY_LOSS", 500.0),
        "max_trades_per_day": _int("MAX_TRADES_PER_DAY", 0),
        "max_concurrent_trades": _int("MAX_CONCURRENT_TRADES", 5),
        "commission_per_lot": _float("COMMISSION_PER_LOT", 7.0),
        "slippage_ticks": _float("SLIPPAGE_TICKS", 2.0),
        "symbols": get_symbols(),
    }


def update_env_values(updates: dict[str, str]) -> None:
    """Update keys in .env file (preserves comments and other keys)."""
    if not _ENV_PATH.exists():
        lines = []
    else:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    for key, val in remaining.items():
        out.append(f"{key}={val}")

    _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    for key, val in updates.items():
        os.environ[key] = val
