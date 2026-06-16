import pandas as pd
import numpy as np
import os
import argparse
import logging
from dotenv import load_dotenv

load_dotenv()

from strategies.registry import get_strategy
from risk_manager import RiskManager
from symbol_specs import (
    calc_gross_pnl_usd,
    get_contract_size,
    get_tick_size,
    tick_value_usd,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class StrategyBacktester:
    """Event-driven backtester — strategy-agnostic via plugin registry."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        commission_per_lot: float = None,
        spread_multiplier: float = 1.0,
        risk_per_trade_cash: float | None = None,
        symbol: str = "GBPUSD",
        symbol_contract_size: float | None = None,
        trade_capital_usd: float | None = None,
        leverage: float | None = None,
        strategy_name: str | None = None,
    ):
        self.symbol = symbol.strip().upper()
        self.strategy = get_strategy(strategy_name)
        resolved_contract = symbol_contract_size if symbol_contract_size is not None else get_contract_size(self.symbol)
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.commission_per_lot = commission_per_lot if commission_per_lot is not None else float(os.getenv("COMMISSION_PER_LOT", 7.0))
        self.slippage_ticks = float(os.getenv("SLIPPAGE_TICKS", 2.0))
        self.spread_multiplier = spread_multiplier
        self.risk_per_trade_cash = risk_per_trade_cash if risk_per_trade_cash is not None else float(os.getenv("RISK_PER_TRADE", 50))
        self.trade_capital_usd = trade_capital_usd if trade_capital_usd is not None else 500.0
        self.leverage = leverage if leverage and leverage > 0 else 100.0
        self.symbol_contract_size = resolved_contract

        self.risk_mgr = RiskManager(
            max_daily_loss=500.0,
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", 0)),
            max_concurrent_trades=1,
            risk_per_trade_cash=self.risk_per_trade_cash,
            symbol_contract_size=resolved_contract,
            trade_capital_usd=self.trade_capital_usd,
            leverage=self.leverage,
            symbol=self.symbol,
            commission_per_lot=self.commission_per_lot,
            slippage_ticks=self.slippage_ticks,
        )

        self.trades = []
        self.equity_curve = []

    def run(self, data_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not os.path.exists(data_path):
            logging.error(f"Data file not found at: {data_path}")
            return pd.DataFrame(), pd.DataFrame()

        logging.info(f"Loading data from {data_path} | strategy={self.strategy.name}")
        df = pd.read_csv(data_path)
        df["time"] = pd.to_datetime(df["time"])
        if (
            self.strategy.name == "holygrail"
            and data_path.upper().endswith("_M5.CSV")
        ):
            df = (
                df.set_index("time")
                .resample("15min")
                .agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "tick_volume": "sum",
                    **({"spread": "mean"} if "spread" in df.columns else {}),
                })
                .dropna()
                .reset_index()
            )
            logging.info("Resampled M5 → M15 for OptMeanRev")
        df = df.sort_values("time").reset_index(drop=True)
        df = self.strategy.prepare(df)

        lookback = self.strategy.lookback_bars()
        warmup = self.strategy.warmup_bars()
        active_position = None
        current_day = None

        logging.info("Starting backtest simulation...")
        for i in range(warmup, len(df)):
            current_bar = df.iloc[i]
            history = df.iloc[max(0, i - lookback) : i]
            signal = self.strategy.evaluate(current_bar, history)

            bar_date = current_bar["time"].date()
            if current_day != bar_date:
                current_day = bar_date
                self.risk_mgr.reset_daily_metrics()

            if active_position is not None:
                is_closed = False
                exit_price = 0.0
                exit_reason = ""

                max_hold = active_position.get("max_hold_bars")
                if max_hold:
                    active_position["bars_held"] = active_position.get("bars_held", 0) + 1
                    if active_position["bars_held"] >= max_hold:
                        is_closed = True
                        exit_price = float(current_bar["close"])
                        exit_reason = "TIME EXIT"

                if not is_closed and active_position["action"] == "BUY":
                    tp_trig = active_position.get("tp_trigger", active_position["tp"])
                    if current_bar["low"] <= active_position["sl"]:
                        is_closed, exit_price, exit_reason = True, active_position["sl"], "STOP LOSS HIT"
                    elif current_bar["high"] >= tp_trig:
                        is_closed, exit_price, exit_reason = (
                            True, min(float(current_bar["high"]), active_position["tp"]), "MEAN TARGET HIT",
                        )
                elif not is_closed and active_position["action"] == "SELL":
                    tp_trig = active_position.get("tp_trigger", active_position["tp"])
                    if current_bar["high"] >= active_position["sl"]:
                        is_closed, exit_price, exit_reason = True, active_position["sl"], "STOP LOSS HIT"
                    elif current_bar["low"] <= tp_trig:
                        is_closed, exit_price, exit_reason = (
                            True, max(float(current_bar["low"]), active_position["tp"]), "MEAN TARGET HIT",
                        )

                if is_closed:
                    gross_pnl = calc_gross_pnl_usd(
                        self.symbol, active_position["action"], active_position["entry_price"],
                        exit_price, active_position["size"], self.symbol_contract_size,
                    )
                    tick_val = tick_value_usd(self.symbol, exit_price, self.symbol_contract_size)
                    slippage_cost = (self.slippage_ticks * 2) * tick_val * active_position["size"]
                    comm = active_position["size"] * self.commission_per_lot
                    net_pnl = gross_pnl - comm - slippage_cost
                    self.balance += net_pnl

                    active_position["exit_time"] = current_bar["time"]
                    active_position["exit_price"] = exit_price
                    active_position["net_pnl"] = net_pnl
                    active_position["result"] = exit_reason
                    self.trades.append(active_position)
                    self.risk_mgr.log_trade_close(net_pnl)
                    active_position = None

            if active_position is None:
                allowed, _ = self.risk_mgr.is_trading_allowed()
                if allowed and signal.action in ("BUY", "SELL"):
                    entry_price = (
                        float(current_bar["open"])
                        if getattr(self.strategy, "entry_at_open", False)
                        else float(current_bar["close"])
                    )
                    lot_size = self.risk_mgr.calculate_lot_size(
                        entry_price=entry_price,
                        sl_price=float(signal.sl),
                    )
                    if lot_size <= 0:
                        continue

                    active_position = {
                        "entry_time": current_bar["time"],
                        "action": signal.action,
                        "entry_price": entry_price,
                        "size": lot_size,
                        "sl": signal.sl,
                        "tp": signal.tp,
                        "tp_trigger": signal.meta.get("tp_trigger", signal.tp),
                        "trigger_level": signal.trigger_level,
                        "reason": signal.reason,
                        "rr_ratio": signal.rr_ratio,
                        "max_hold_bars": signal.meta.get("max_hold_bars"),
                        "bars_held": 0,
                        "exit_time": None,
                        "exit_price": 0.0,
                        "net_pnl": 0.0,
                        "result": "OPEN",
                    }
                    self.risk_mgr.log_trade_open()

            floating_pnl = 0.0
            if active_position is not None:
                floating_pnl = calc_gross_pnl_usd(
                    self.symbol, active_position["action"], active_position["entry_price"],
                    float(current_bar["close"]), active_position["size"], self.symbol_contract_size,
                )

            self.equity_curve.append({
                "time": current_bar["time"],
                "balance": self.balance,
                "equity": self.balance + floating_pnl,
            })

        return pd.DataFrame(self.trades), pd.DataFrame(self.equity_curve)

    def print_performance_summary(self, trades_df: pd.DataFrame, equity_df: pd.DataFrame):
        if trades_df.empty:
            print("\n" + "=" * 50)
            print(" BACKTEST COMPLETED - ZERO TRADES EXECUTED ")
            print("=" * 50)
            return

        total_trades = len(trades_df)
        win_trades = trades_df[trades_df["net_pnl"] > 0]
        loss_trades = trades_df[trades_df["net_pnl"] <= 0]
        win_rate = len(win_trades) / total_trades * 100
        gross_profit = win_trades["net_pnl"].sum()
        gross_loss = abs(loss_trades["net_pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf
        net_profit = trades_df["net_pnl"].sum()
        return_pct = (net_profit / self.initial_balance) * 100

        trades_df = trades_df.copy()
        trades_df["trade_date"] = pd.to_datetime(trades_df["entry_time"]).dt.date
        days_with_trades = trades_df["trade_date"].nunique()
        trades_per_day = total_trades / days_with_trades if days_with_trades else 0.0

        equity_df = equity_df.copy()
        equity_df["peak"] = equity_df["equity"].cummax()
        equity_df["drawdown"] = (equity_df["peak"] - equity_df["equity"]) / equity_df["peak"] * 100
        max_dd = equity_df["drawdown"].max()

        print("\n" + "=" * 60)
        print(f"  BACKTEST REPORT | strategy={self.strategy.name} | OptMeanRev")
        print("=" * 60)
        print(f"Initial Capital   : ${self.initial_balance:,.2f}")
        print(f"Final Balance     : ${self.balance:,.2f}")
        print(f"Net Return        : ${net_profit:+,.2f} ({return_pct:+.2f}%)")
        print(f"Max Drawdown      : {max_dd:.2f}%")
        print(f"Total Trades      : {total_trades}")
        print(f"Active Days       : {days_with_trades}")
        print(f"Avg Trades/Day    : {trades_per_day:.2f}")
        print(f"Win Rate          : {win_rate:.2f}%")
        print(f"Profit Factor     : {profit_factor:.2f}")
        print("=" * 60 + "\n")


# Backward-compatible alias
InstitutionalFlowBacktester = StrategyBacktester


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("--data", type=str, default="data/XAUUSD_M1.csv")
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--strategy", type=str, default=None)
    parser.add_argument("--contract-size", type=float, default=None)
    parser.add_argument("--risk", type=float, default=50.0)
    parser.add_argument("--trade-capital", type=float, default=500.0)
    parser.add_argument("--leverage", type=float, default=100.0)
    parser.add_argument("--output", type=str, default="data/backtest_trades.csv")
    args = parser.parse_args()

    bt = StrategyBacktester(
        initial_balance=10000.0,
        risk_per_trade_cash=args.risk,
        trade_capital_usd=args.trade_capital,
        leverage=args.leverage,
        symbol=args.symbol,
        symbol_contract_size=args.contract_size,
        strategy_name=args.strategy,
    )
    trades, equity = bt.run(args.data)

    if not trades.empty:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        trades.to_csv(args.output, index=False)
        equity.to_csv(args.output.replace("_trades.csv", "_equity.csv"), index=False)
        bt.print_performance_summary(trades, equity)
    else:
        print("Backtest finished with no trades.")
