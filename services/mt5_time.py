"""Consistent MT5 timestamp handling (Unix epoch = UTC)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def mt5_ts_to_datetime(ts: int | float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def mt5_ts_to_str(ts: int | float | None) -> str:
    dt = mt5_ts_to_datetime(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def bar_time_to_str(bar_time) -> str:
    """Format MT5/pandas bar timestamps (UTC, naive)."""
    if bar_time is None:
        return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(bar_time, pd.Timestamp):
        if bar_time.tzinfo is not None:
            return bar_time.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")
        return bar_time.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(bar_time, datetime):
        return bar_time.strftime("%Y-%m-%d %H:%M:%S")
    return str(bar_time)[:19]
