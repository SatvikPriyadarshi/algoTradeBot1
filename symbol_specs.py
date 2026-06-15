"""Per-symbol contract, tick, and P&L specs for consistent risk across assets."""

from __future__ import annotations

DEFAULT_FX_CONTRACT = 100_000.0

# Explicit overrides for non-standard instruments.
_SYMBOL_OVERRIDES: dict[str, dict[str, float]] = {
    "XAUUSD": {"contract_size": 100.0, "tick_size": 0.01},
    "XAGUSD": {"contract_size": 5000.0, "tick_size": 0.001},
    "BTCUSD": {"contract_size": 1.0, "tick_size": 0.01},
}


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def get_symbol_specs(symbol: str) -> dict[str, float]:
    sym = normalize_symbol(symbol)
    if sym in _SYMBOL_OVERRIDES:
        return _SYMBOL_OVERRIDES[sym].copy()

    if sym.endswith("JPY"):
        return {"contract_size": DEFAULT_FX_CONTRACT, "tick_size": 0.001}

    return {"contract_size": DEFAULT_FX_CONTRACT, "tick_size": 0.00001}


def get_contract_size(symbol: str) -> float:
    return get_symbol_specs(symbol)["contract_size"]


def get_tick_size(symbol: str) -> float:
    return get_symbol_specs(symbol)["tick_size"]


def _reference_price(symbol: str, entry_price: float, exit_price: float) -> float:
    ref = (entry_price + exit_price) / 2.0
    return ref if ref > 0 else max(entry_price, exit_price, 1.0)


def tick_value_usd(
    symbol: str,
    price: float,
    contract_size: float | None = None,
) -> float:
    """USD value of one tick move for 1.0 lot."""
    specs = get_symbol_specs(symbol)
    sym = normalize_symbol(symbol)
    cs = contract_size if contract_size is not None else specs["contract_size"]
    tick = specs["tick_size"]

    if sym.endswith("USD") and not sym.startswith("USD"):
        return cs * tick

    if price <= 0:
        return cs * tick

    return (cs * tick) / price


def calc_gross_pnl_usd(
    symbol: str,
    action: str,
    entry_price: float,
    exit_price: float,
    lot_size: float,
    contract_size: float | None = None,
) -> float:
    """Calculate gross P&L in USD for a closed trade."""
    specs = get_symbol_specs(symbol)
    cs = contract_size if contract_size is not None else specs["contract_size"]
    sym = normalize_symbol(symbol)

    if action == "BUY":
        price_diff = exit_price - entry_price
    else:
        price_diff = entry_price - exit_price

    if sym.endswith("USD") and not sym.startswith("USD"):
        return price_diff * lot_size * cs

    ref_price = _reference_price(sym, entry_price, exit_price)
    return (price_diff * lot_size * cs) / ref_price


def notional_usd_per_lot(
    symbol: str,
    price: float,
    contract_size: float | None = None,
) -> float:
    """Approximate USD notional value of 1.0 lot (for margin sizing)."""
    specs = get_symbol_specs(symbol)
    sym = normalize_symbol(symbol)
    cs = contract_size if contract_size is not None else specs["contract_size"]

    if price <= 0:
        return cs

    if sym.endswith("USD") and not sym.startswith("USD"):
        return price * cs

    if sym.startswith("USD") and len(sym) == 6:
        return cs

    if sym.endswith("JPY"):
        return cs / price

    return price * cs


def margin_usd_per_lot(
    symbol: str,
    price: float,
    leverage: float,
    contract_size: float | None = None,
) -> float:
    """USD margin required for 1.0 lot at the given leverage."""
    if leverage <= 0:
        leverage = 1.0
    return notional_usd_per_lot(symbol, price, contract_size) / leverage


def max_lots_from_trade_capital(
    symbol: str,
    entry_price: float,
    trade_capital_usd: float,
    leverage: float,
    contract_size: float | None = None,
) -> float:
    """Max lots affordable within the user's USD allocation (margin budget)."""
    if trade_capital_usd <= 0:
        return 0.0

    margin_per_lot = margin_usd_per_lot(symbol, entry_price, leverage, contract_size)
    if margin_per_lot <= 0:
        return 0.0

    return trade_capital_usd / margin_per_lot
