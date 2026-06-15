"""Anti-hedge — block long+short on the same symbol only (true MT5 hedge)."""

from __future__ import annotations

from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore


def _mt5_exposure(symbol: str) -> tuple[float, float, bool]:
    """Return (buy_lots, sell_lots, is_hedged) for one symbol on MT5."""
    if mt5 is None:
        return 0.0, 0.0, False
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return 0.0, 0.0, False

    buy_lots = 0.0
    sell_lots = 0.0
    for pos in positions:
        if pos.type == mt5.POSITION_TYPE_BUY:
            buy_lots += float(pos.volume)
        else:
            sell_lots += float(pos.volume)
    return buy_lots, sell_lots, buy_lots > 0 and sell_lots > 0


def validate_new_entry(
    symbol: str,
    action: str,
    active_positions: dict[str, Any | None],
    *,
    check_mt5: bool = True,
) -> tuple[bool, str]:
    """
    Block hedging on the same pair only:
    - No BUY + SELL on the same symbol at once
    - No second position on a symbol that already has one
    Different pairs may be BUY and SELL at the same time (e.g. GBPUSD long + EURUSD short).
    """
    symbol = symbol.strip().upper()
    action = action.strip().upper()
    if action not in ("BUY", "SELL"):
        return False, "Invalid action"

    if check_mt5 and mt5 is not None:
        buy_lots, sell_lots, hedged = _mt5_exposure(symbol)
        if hedged:
            return False, f"Hedge blocked: {symbol} has BUY and SELL open on MT5"
        if buy_lots > 0 and action == "SELL":
            return False, f"Hedge blocked: {symbol} already LONG on MT5 ({buy_lots:.2f} lots)"
        if sell_lots > 0 and action == "BUY":
            return False, f"Hedge blocked: {symbol} already SHORT on MT5 ({sell_lots:.2f} lots)"
        if buy_lots > 0 and action == "BUY":
            return False, f"Already LONG {symbol} on MT5 ({buy_lots:.2f} lots)"
        if sell_lots > 0 and action == "SELL":
            return False, f"Already SHORT {symbol} on MT5 ({sell_lots:.2f} lots)"

    pos = active_positions.get(symbol)
    if pos is None:
        return True, ""

    open_action = str(pos.get("action", "")).upper()
    if open_action == action:
        return False, f"Already in {action} on {symbol}"
    return False, f"Hedge blocked: {symbol} is {open_action}, cannot open {action}"


def audit_mt5_book(symbols: list[str]) -> list[str]:
    """Warn only if the same symbol has both BUY and SELL on MT5."""
    warnings: list[str] = []
    for sym in symbols:
        buy_lots, sell_lots, hedged = _mt5_exposure(sym)
        if hedged:
            warnings.append(f"{sym}: BUY {buy_lots:.2f} and SELL {sell_lots:.2f} on same pair — close one side")
    return warnings
