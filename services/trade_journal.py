"""Trade journal helpers — duration, stats, normalization."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_trade_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def format_duration(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def timeframe_minutes(tf: str | None) -> int:
    mapping = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H2": 120, "H4": 240, "D1": 1440}
    return mapping.get((tf or "M15").upper(), 15)


def enrich_trade_record(trade: dict, *, default_timeframe: str = "M15") -> dict:
    """Add duration / outcome fields expected by the journal UI."""
    out = dict(trade)
    entry_dt = parse_trade_time(out.get("entry_time"))
    exit_dt = parse_trade_time(out.get("exit_time"))

    tf = out.get("timeframe") or default_timeframe
    bars = out.get("bars_held")
    if bars is None and entry_dt and exit_dt:
        bar_min = timeframe_minutes(tf)
        bars = max(1, int(round((exit_dt - entry_dt).total_seconds() / 60 / bar_min)))

    duration_seconds = out.get("duration_seconds")
    if duration_seconds is None and entry_dt and exit_dt:
        duration_seconds = max(0, int((exit_dt - entry_dt).total_seconds()))
    elif duration_seconds is None and bars is not None:
        duration_seconds = int(bars) * timeframe_minutes(tf) * 60

    out["bars_held"] = bars
    out["duration_seconds"] = duration_seconds
    out["duration_label"] = out.get("duration_label") or format_duration(duration_seconds or 0)
    out["timeframe"] = tf

    result = (out.get("result") or "").upper()
    if "STOP" in result or result == "SL":
        out["exit_type"] = "SL"
    elif "MEAN" in result or "TARGET" in result or result == "TP":
        out["exit_type"] = "TP"
    elif "TIME" in result:
        out["exit_type"] = "TIME"
    else:
        out["exit_type"] = out.get("exit_type") or "OTHER"

    pnl = float(out.get("net_pnl") or 0)
    out["outcome"] = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BE")
    return out


def is_live_trade(trade: dict) -> bool:
    return (trade.get("mode") or "").lower() == "live"


def live_trades_only(trades: list[dict]) -> list[dict]:
    return [t for t in trades if is_live_trade(t)]


def compute_trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "avg_duration_seconds": 0,
            "avg_duration_label": "—",
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "avg_bars_held": 0.0,
            "sl_exits": 0,
            "tp_exits": 0,
            "time_exits": 0,
        }

    enriched = [enrich_trade_record(t) for t in trades]
    wins = [t for t in enriched if t["outcome"] == "WIN"]
    losses = [t for t in enriched if t["outcome"] == "LOSS"]
    be = [t for t in enriched if t["outcome"] == "BE"]

    gross_profit = sum(float(t["net_pnl"]) for t in wins)
    gross_loss = abs(sum(float(t["net_pnl"]) for t in losses))
    net_pnl = sum(float(t.get("net_pnl") or 0) for t in enriched)
    pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    durations = [t["duration_seconds"] for t in enriched if t.get("duration_seconds")]
    avg_dur = sum(durations) / len(durations) if durations else 0
    bars = [t["bars_held"] for t in enriched if t.get("bars_held") is not None]

    return {
        "total_trades": len(enriched),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": round(len(wins) / len(enriched) * 100, 1) if enriched else 0.0,
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(pf, 2),
        "avg_duration_seconds": int(avg_dur),
        "avg_duration_label": format_duration(avg_dur),
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "avg_bars_held": round(sum(bars) / len(bars), 1) if bars else 0.0,
        "sl_exits": sum(1 for t in enriched if t.get("exit_type") == "SL"),
        "tp_exits": sum(1 for t in enriched if t.get("exit_type") == "TP"),
        "time_exits": sum(1 for t in enriched if t.get("exit_type") == "TIME"),
    }
