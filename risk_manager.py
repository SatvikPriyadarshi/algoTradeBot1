import logging
import math
import os

from symbol_specs import max_lots_from_trade_capital, margin_usd_per_lot, tick_value_usd


class RiskManager:
    """
    Handles institutional-grade risk management.
    Controls dynamic position sizing, daily drawdowns, trade count limits, and session filters.
    """
    def __init__(self,
                 max_daily_loss: float = 500.0,
                 max_trades_per_day: int = 5,
                 max_concurrent_trades: int = 1,
                 risk_per_trade_cash: float = 100.0,
                 symbol_contract_size: float = 100.0,
                 trade_capital_usd: float | None = None,
                 leverage: float = 100.0,
                 symbol: str = "XAUUSD",
                 commission_per_lot: float | None = None,
                 slippage_ticks: float | None = None,
                 ):
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day
        self.max_concurrent_trades = max_concurrent_trades
        self.risk_per_trade_cash = risk_per_trade_cash
        self.symbol_contract_size = symbol_contract_size
        # Margin budget per trade (backtest UI / simulated cap). Live engine sets from MT5.
        self.trade_capital_usd = trade_capital_usd
        self.leverage = leverage if leverage > 0 else 100.0
        self.symbol = symbol.strip().upper()
        self.commission_per_lot = (
            commission_per_lot if commission_per_lot is not None
            else float(os.getenv("COMMISSION_PER_LOT", 7.0))
        )
        self.slippage_ticks = (
            slippage_ticks if slippage_ticks is not None
            else float(os.getenv("SLIPPAGE_TICKS", 2.0))
        )

        self.daily_loss_accumulated = 0.0
        self.trades_taken_today = 0
        self.active_positions_count = 0

    def reset_daily_metrics(self):
        """Reset daily tracking metrics. Should be called at the start of a new trading day."""
        self.daily_loss_accumulated = 0.0
        self.trades_taken_today = 0
        logging.info("Risk Manager: Daily metrics reset successfully.")

    def log_trade_close(self, net_pnl: float):
        """Update risk limits based on closed trade results."""
        self.active_positions_count = max(0, self.active_positions_count - 1)
        if net_pnl < 0:
            self.daily_loss_accumulated += abs(net_pnl)
        else:
            self.daily_loss_accumulated -= net_pnl

    def log_trade_open(self):
        """Update metrics for new trade opening."""
        self.active_positions_count += 1
        self.trades_taken_today += 1

    def is_trading_allowed(self) -> tuple[bool, str]:
        if self.daily_loss_accumulated >= self.max_daily_loss:
            return False, f"Daily Loss Limit Exceeded (${self.daily_loss_accumulated:.2f} >= ${self.max_daily_loss:.2f})"
        if self.max_trades_per_day > 0 and self.trades_taken_today >= self.max_trades_per_day:
            return False, f"Max Daily Trade Count Reached ({self.trades_taken_today} >= {self.max_trades_per_day})"
        if self.active_positions_count >= self.max_concurrent_trades:
            return False, f"Max Concurrent Trades Limit Reached ({self.active_positions_count} >= {self.max_concurrent_trades})"
        return True, ""

    def _net_loss_per_lot_at_sl(self, entry_price: float, sl_distance: float) -> float:
        """Estimated net USD loss for 1.0 lot if stop loss is hit (incl. commission + slippage)."""
        tick_val = tick_value_usd(self.symbol, entry_price, self.symbol_contract_size)
        slippage_per_lot = (self.slippage_ticks * 2) * tick_val
        price_risk_per_lot = sl_distance * self.symbol_contract_size
        return price_risk_per_lot + self.commission_per_lot + slippage_per_lot

    def calculate_lot_size(self, entry_price: float, sl_price: float, pip_value: float = 1.0) -> float:
        """
        Size so NET loss at stop loss (after commission + slippage) <= risk_per_trade_cash.
        When margin budget is set (MT5 free margin or backtest capital), lot is also capped
        so required margin does not exceed that budget.
        """
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0 or entry_price <= 0:
            logging.warning("Risk Manager: Invalid entry/SL prices. Skipping trade.")
            return 0.0

        net_loss_per_lot = self._net_loss_per_lot_at_sl(entry_price, sl_distance)
        if net_loss_per_lot <= 0:
            return 0.0

        lot_from_risk = self.risk_per_trade_cash / net_loss_per_lot
        lot_size = lot_from_risk

        if self.trade_capital_usd and self.trade_capital_usd > 0:
            lot_from_capital = max_lots_from_trade_capital(
                self.symbol,
                entry_price,
                self.trade_capital_usd,
                self.leverage,
                self.symbol_contract_size,
            )
            margin_min_lot = margin_usd_per_lot(
                self.symbol,
                entry_price,
                self.leverage,
                self.symbol_contract_size,
            ) * 0.01
            if margin_min_lot > self.trade_capital_usd:
                logging.info(
                    f"Risk Manager: ${self.trade_capital_usd:.2f} margin budget cannot cover min 0.01 lot "
                    f"(margin ${margin_min_lot:.2f}). Skipping trade."
                )
                return 0.0

            lot_size = min(lot_from_risk, lot_from_capital)

        # Floor so net loss never exceeds max risk after rounding
        lot_size = math.floor(lot_size * 100) / 100
        if lot_size < 0.01:
            logging.info("Risk Manager: Calculated lot size below minimum. Skipping trade.")
            return 0.0

        est_net_loss = lot_size * net_loss_per_lot
        logging.info(
            f"Risk Manager: SL dist {sl_distance:.5f} | lot {lot_size:.2f} | "
            f"est. net loss at SL ${est_net_loss:.2f} (max ${self.risk_per_trade_cash:.2f})"
        )
        return lot_size
