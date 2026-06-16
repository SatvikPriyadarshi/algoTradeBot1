"""
Dynamic Oracle Engine — Main execution server.
Manages MT5 connection, multi-symbol scanning, trade execution, and WebSocket dashboard.

Architecture:
  - FastAPI serves the dashboard UI and REST/WebSocket APIs on the main thread.
  - A single daemon thread ("AlgoExecutionThread") owns ALL MetaTrader5 API calls.
  - Credential handoff: MT5 login from .env on startup (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER).
  - Dashboard is optional — scanning and trading run in the execution thread alone.
  - Data download queue: /api/backtest requests data via pending_downloads;
    the execution thread fulfills them (since MT5 is single-threaded).
  - State is shared via GlobalState protected by threading.Lock.
"""

import asyncio
import threading
import json
import os
import sys
import time
import argparse
import logging
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager
import pandas as pd
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
import uvicorn
import MetaTrader5 as mt5
from dotenv import load_dotenv

# ─── Bootstrap ───────────────────────────────────────────────────────────────
load_dotenv()
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/execution_engine.log", mode="a", encoding="utf-8"),
    ]
)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from strategies.registry import get_strategy
from strategies.base import BaseStrategy
from risk_manager import RiskManager
from services.engine_config import get_risk_settings, get_symbols, update_env_values, get_mt5_credentials, get_engine_mode
from services.trade_journal import (
    enrich_trade_record,
    compute_trade_stats,
    parse_trade_time,
    format_duration,
    live_trades_only,
    is_live_trade,
)
from services.mt5_time import mt5_ts_to_str, mt5_ts_to_datetime, bar_time_to_str
from services.anti_hedge import validate_new_entry, audit_mt5_book
from symbol_specs import get_contract_size, get_symbol_specs, calc_gross_pnl_usd, tick_value_usd

# ─── Global Shared State ────────────────────────────────────────────────────
class GlobalState:
    """Thread-safe state shared between the FastAPI thread and the execution thread."""

    def __init__(self):
        self.lock = threading.Lock()

        # Account
        self.balance = 0.0
        self.equity = 0.0
        self.margin_free = 0.0
        self.account_leverage = 0
        self.pnl_today = 0.0

        # Multi-symbol config (from .env)
        cfg = get_risk_settings()
        self.symbols = cfg["symbols"]
        self.max_concurrent_trades = cfg["max_concurrent_trades"]
        self.engine_timeframe = cfg["timeframe"]
        self.risk_settings = cfg

        # Connection
        self.status = "CONNECTING"
        self.error_msg = "Starting MT5 login from .env..."
        self.mode = get_engine_mode()
        self.mt5_initialized = False       # MT5 terminal is open
        self.logged_in = False             # User has logged into an MT5 account
        self.account_name = ""

        # Credential handoff queue
        self.pending_login = None          # {login, password, server, live} or None

        # Data download queue (backtest → execution thread)
        self.pending_downloads = []        # [{symbol, timeframe, bars, csv_path, done: Event}]

        # Per-symbol state
        self.active_positions = {sym: None for sym in self.symbols}
        self.active_swing_highs = {sym: 0.0 for sym in self.symbols}
        self.active_swing_lows = {sym: 0.0 for sym in self.symbols}
        self.active_poc = {sym: 0.0 for sym in self.symbols}
        self.last_prices = {sym: 0.0 for sym in self.symbols}
        self.bands_updated_at = {sym: 0.0 for sym in self.symbols}

        boot_strategy = get_strategy()
        default_rules = boot_strategy.default_rules()
        self.rules = {sym: dict(default_rules) for sym in self.symbols}
        self.strategy_name = boot_strategy.name

        # Trade history
        self.trades = []
        self.calendar = {}  # date_str -> net_pnl


global_state = GlobalState()
active_websockets: list[WebSocket] = []

# ─── Trade History Persistence ───────────────────────────────────────────────
HISTORY_FILE = "data/live_trades.json"

def _rebuild_calendar_from_trades(trades: list[dict]) -> None:
    cal: dict[str, float] = {}
    for t in trades:
        exit_time_str = t.get("exit_time")
        if exit_time_str:
            date_str = str(exit_time_str).split(" ")[0]
            cal[date_str] = cal.get(date_str, 0.0) + float(t.get("net_pnl", 0) or 0)
    global_state.calendar = cal


def _persist_trade_history() -> None:
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(global_state.trades, f, indent=2, default=str)
    except Exception as e:
        logging.error(f"Failed to persist trade: {e}")


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r") as f:
            raw = json.load(f)
        live = live_trades_only(raw)
        dropped = len(raw) - len(live)
        global_state.trades = [
            enrich_trade_record(t, default_timeframe=global_state.engine_timeframe)
            for t in live
        ]
        _rebuild_calendar_from_trades(global_state.trades)
        if dropped:
            logging.info(f"Removed {dropped} dry-run trade(s) from journal history.")
        _persist_trade_history()
        logging.info(f"Loaded {len(global_state.trades)} live MT5 trade(s) from history.")
    except Exception as e:
        logging.error(f"Error loading trade history: {e}")


def save_trade_to_history(trade: dict):
    if not is_live_trade(trade):
        return

    enriched = enrich_trade_record(trade, default_timeframe=global_state.engine_timeframe)
    position_id = enriched.get("position_id")
    if position_id is not None:
        for existing in global_state.trades:
            if existing.get("position_id") == position_id:
                return

    enriched["trade_id"] = enriched.get("trade_id") or (len(global_state.trades) + 1)
    global_state.trades.append(enriched)
    if enriched.get("exit_time"):
        date_str = str(enriched["exit_time"]).split(" ")[0]
        global_state.calendar[date_str] = global_state.calendar.get(date_str, 0.0) + enriched.get("net_pnl", 0.0)
    _persist_trade_history()


# ─── Safe JSON Serializer ───────────────────────────────────────────────────
def safe_serialize(obj):
    """Convert numpy/pandas types to Python-native for JSON serialization."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp, datetime)):
        return str(obj)
    return str(obj)


# ─── WebSocket Broadcasting ─────────────────────────────────────────────────
async def broadcast_state():
    if not active_websockets:
        return

    with global_state.lock:
        journal = live_trades_only(global_state.trades)
        payload = {
            "balance": round(global_state.balance, 2),
            "equity": round(global_state.equity, 2),
            "pnl_today": round(global_state.pnl_today, 2),
            "status": global_state.status,
            "error": global_state.error_msg,
            "active_positions": global_state.active_positions,
            "symbols": global_state.symbols,
            "mode": global_state.mode,
            "swing_highs": global_state.active_swing_highs,
            "swing_lows": global_state.active_swing_lows,
            "poc_levels": global_state.active_poc,
            "last_prices": global_state.last_prices,
            "bands_updated_at": global_state.bands_updated_at,
            "strategy": global_state.strategy_name,
            "timeframe": global_state.engine_timeframe,
            "risk_per_trade": global_state.risk_settings.get("risk_per_trade", 50),
            "margin_free": round(global_state.margin_free, 2),
            "account_leverage": global_state.account_leverage,
            "trade_stats": compute_trade_stats(journal),
            "trades": journal[-200:],
            "trades_total": len(journal),
            "calendar": global_state.calendar,
            "rules": global_state.rules,
            "account_name": global_state.account_name,
            "heartbeat": time.time()
        }

    payload_json = json.dumps(payload, default=safe_serialize)

    dead = []
    for ws in active_websockets:
        try:
            await ws.send_text(payload_json)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in active_websockets:
            active_websockets.remove(ws)


async def state_broadcast_loop():
    while True:
        await broadcast_state()
        await asyncio.sleep(0.5)


# ─── FastAPI Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(state_broadcast_loop())
    yield
    task.cancel()

app = FastAPI(title="Dynamic Oracle Engine", lifespan=lifespan)


# ─── API Schemas ─────────────────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str
    bars: int
    initial_balance: float
    trade_capital: float
    max_risk: float
    leverage: float = 100.0


# ─── REST Endpoints ──────────────────────────────────────────────────────────
@app.get("/api/mt5/status")
def mt5_status():
    with global_state.lock:
        return {
            "status": global_state.status,
            "logged_in": global_state.logged_in,
            "mode": global_state.mode,
            "error": global_state.error_msg,
            "account_name": global_state.account_name,
            "balance": global_state.balance,
            "equity": global_state.equity,
            "auto_login": get_mt5_credentials() is not None,
            "server": os.getenv("MT5_SERVER", ""),
            "login": os.getenv("MT5_LOGIN", ""),
        }


class RiskConfigUpdate(BaseModel):
    risk_per_trade: float


@app.get("/api/config")
def get_config():
    cfg = get_risk_settings()
    with global_state.lock:
        cfg["balance"] = global_state.balance
        cfg["equity"] = global_state.equity
        cfg["margin_free"] = global_state.margin_free
        cfg["account_leverage"] = global_state.account_leverage
        cfg["mode"] = global_state.mode

    # Make it obvious what drives LIVE lot sizing vs backtest simulation
    cfg["live_sizing"] = {
        "risk_per_trade": cfg["risk_per_trade"],
        "margin_source": "mt5",
        "leverage_source": "mt5",
        "margin_free": cfg.get("margin_free", 0),
        "account_leverage": cfg.get("account_leverage", 0),
        "note": "Live/dry-run lots = RISK_PER_TRADE / SL distance, capped by MT5 free margin.",
    }
    cfg["backtest_sizing"] = {
        "trade_capital": cfg["backtest_trade_capital"],
        "leverage": cfg["backtest_leverage"],
        "note": "Used only by POST /api/backtest — not live execution.",
    }
    return {"status": "OK", "config": cfg}


@app.post("/api/config/risk")
def update_risk_config(req: RiskConfigUpdate):
    if req.risk_per_trade <= 0:
        return JSONResponse(status_code=400, content={"status": "ERROR", "message": "Risk must be positive."})

    update_env_values({
        "RISK_PER_TRADE": str(req.risk_per_trade),
    })
    cfg = get_risk_settings()
    with global_state.lock:
        global_state.risk_settings = cfg
    logging.info(f"Risk config updated: ${req.risk_per_trade} per trade (lot size from SL + MT5 margin)")
    return {
        "status": "OK",
        "config": cfg,
        "message": "Saved to .env — lot size = risk ÷ SL distance, capped by MT5 free margin.",
    }


@app.get("/api/trades")
def get_trades(
    limit: int = Query(500, ge=1, le=5000),
    symbol: str | None = None,
    exit_type: str | None = None,
):
    with global_state.lock:
        all_live = live_trades_only(global_state.trades)
    if symbol:
        sym = symbol.strip().upper()
        rows = [t for t in all_live if (t.get("symbol") or "").upper() == sym]
    else:
        rows = list(all_live)
    if exit_type:
        et = exit_type.strip().upper()
        rows = [t for t in rows if (t.get("exit_type") or "").upper() == et]
    rows = [enrich_trade_record(t, default_timeframe=global_state.engine_timeframe) for t in rows[-limit:]]
    return {
        "status": "OK",
        "total": len(all_live),
        "count": len(rows),
        "stats": compute_trade_stats(all_live),
        "trades": rows,
    }


@app.get("/api/trades/export")
def export_trades_csv():
    with global_state.lock:
        rows = [
            enrich_trade_record(t, default_timeframe=global_state.engine_timeframe)
            for t in live_trades_only(global_state.trades)
        ]
    headers = [
        "trade_id", "symbol", "mode", "action", "size", "entry_time", "exit_time",
        "duration_label", "bars_held", "entry_price", "exit_price", "sl", "tp",
        "result", "exit_type", "outcome", "net_pnl", "reason", "timeframe",
    ]
    lines = [",".join(headers)]
    for t in rows:
        lines.append(",".join(
            str(t.get(h, "")).replace(",", ";") for h in headers
        ))
    body = "\n".join(lines)
    filename = f"trade_journal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/strategies")
def list_strategy_plugins():
    from strategies.registry import list_strategies
    return {"active": os.getenv("STRATEGY", "holygrail"), "available": list_strategies()}


@app.get("/api/symbol-specs/{symbol}")
def symbol_specs(symbol: str):
    specs = get_symbol_specs(symbol)
    return {
        "symbol": symbol.strip().upper(),
        "contract_size": specs["contract_size"],
        "tick_size": specs["tick_size"],
    }


@app.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    logging.info(f"Backtest request: symbol={req.symbol}, tf={req.timeframe}, bars={req.bars}")

    csv_path = f"data/{req.symbol}_{req.timeframe}.csv"

    # Check if MT5 terminal is running (download runs on execution thread)
    with global_state.lock:
        mt5_initialized = global_state.mt5_initialized

    download_error = None
    if mt5_initialized:
        done_event = threading.Event()
        download_req = {
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "bars": req.bars,
            "csv_path": csv_path,
            "done": done_event,
            "error": None,
        }
        with global_state.lock:
            global_state.pending_downloads.append(download_req)
        done_event.wait(timeout=45.0)
        download_error = download_req.get("error")

    if not os.path.exists(csv_path):
        if not mt5_initialized:
            message = "MT5 is not connected to the engine. Open MetaTrader 5, then restart python engine.py."
        elif download_error:
            message = download_error
        else:
            with global_state.lock:
                logged_in = global_state.logged_in
            if not logged_in:
                message = (
                    f"No data for {req.symbol}_{req.timeframe}. "
                    "Log in via Connect MT5 on the Live tab (or log in inside MT5), then retry."
                )
            else:
                message = (
                    f"Could not download {req.symbol}_{req.timeframe}. "
                    "Open that symbol/timeframe chart in MT5, scroll back to load history, then retry."
                )
        return JSONResponse(status_code=400, content={"status": "ERROR", "message": message})

    from backtester import StrategyBacktester

    if req.max_risk <= 0:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "Max risk must be greater than zero."},
        )
    if req.trade_capital <= 0:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "Trade capital must be greater than zero."},
        )
    if req.max_risk > req.trade_capital:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "Max risk cannot exceed trade capital."},
        )
    if req.trade_capital > req.initial_balance:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "Trade capital cannot exceed starting balance."},
        )

    contract_size = get_contract_size(req.symbol)
    logging.info(
        f"Backtest {req.symbol}: capital=${req.trade_capital:.2f}, "
        f"max_risk=${req.max_risk:.2f}, leverage=1:{req.leverage:.0f}, contract={contract_size}"
    )

    backtester = StrategyBacktester(
        initial_balance=req.initial_balance,
        commission_per_lot=float(os.getenv("COMMISSION_PER_LOT", 7.0)),
        risk_per_trade_cash=req.max_risk,
        trade_capital_usd=req.trade_capital,
        leverage=req.leverage,
        symbol=req.symbol,
        symbol_contract_size=contract_size,
        strategy_name=get_risk_settings()["strategy"],
    )

    try:
        trades_df, equity_df = backtester.run(csv_path)

        trades = []
        if not trades_df.empty:
            trades_df = trades_df.fillna("")
            trades = trades_df.to_dict(orient="records")
            for t in trades:
                t['entry_time'] = str(t.get('entry_time', ''))
                t['exit_time'] = str(t.get('exit_time', ''))

        equity = []
        if not equity_df.empty:
            equity_df = equity_df.fillna("")
            equity = equity_df.to_dict(orient="records")
            for eq in equity:
                eq['time'] = str(eq.get('time', ''))

        total_trades = len(trades_df) if not trades_df.empty else 0
        if total_trades > 0:
            win_trades = trades_df[trades_df['net_pnl'] > 0]
            loss_trades = trades_df[trades_df['net_pnl'] <= 0]
            win_rate = len(win_trades) / total_trades * 100
            gross_profit = float(win_trades['net_pnl'].sum())
            gross_loss = float(abs(loss_trades['net_pnl'].sum()))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.9
            net_profit = float(trades_df['net_pnl'].sum())
            return_pct = (net_profit / req.initial_balance) * 100

            eq_calc = equity_df.copy()
            eq_calc['equity'] = pd.to_numeric(eq_calc['equity'], errors='coerce')
            eq_calc['peak'] = eq_calc['equity'].cummax()
            eq_calc['drawdown'] = (eq_calc['peak'] - eq_calc['equity']) / eq_calc['peak'] * 100
            max_dd = float(eq_calc['drawdown'].max())
            trades_df = trades_df.copy()
            trades_df["trade_date"] = pd.to_datetime(trades_df["entry_time"]).dt.date
            active_days = int(trades_df["trade_date"].nunique())
            avg_trades_per_day = total_trades / active_days if active_days else 0.0
        else:
            win_rate = 0.0
            profit_factor = 0.0
            net_profit = 0.0
            return_pct = 0.0
            max_dd = 0.0
            active_days = 0
            avg_trades_per_day = 0.0

        return {
            "status": "SUCCESS",
            "summary": {
                "net_profit": net_profit,
                "return_pct": return_pct,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "max_drawdown": max_dd,
                "total_trades": total_trades,
                "active_days": active_days,
                "avg_trades_per_day": round(avg_trades_per_day, 2),
                "final_balance": req.initial_balance + net_profit
            },
            "trades": trades,
            "equity": equity
        }
    except Exception as e:
        logging.error(f"Backtest error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "message": str(e)}
        )


@app.get("/api/state")
def get_state():
    with global_state.lock:
        return {
            "balance": global_state.balance,
            "equity": global_state.equity,
            "status": global_state.status,
            "symbols": global_state.symbols,
            "mode": global_state.mode,
            "active_positions": global_state.active_positions,
            "calendar": global_state.calendar,
            "trades_count": len(global_state.trades)
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    logging.info(f"WebSocket client connected. Total: {len(active_websockets)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
        logging.info(f"WebSocket client disconnected. Total: {len(active_websockets)}")


# Static files served last (catch-all)
app.mount("/", StaticFiles(directory="templates", html=True), name="templates")


def _verify_mt5_connection(expected_login: int | None = None) -> tuple[bool, str]:
    """Confirm MT5 terminal is up, broker-linked, and an account session is active."""
    if not mt5.initialize():
        err = mt5.last_error()
        return False, f"MT5 init failed: {err}"

    terminal = mt5.terminal_info()
    if terminal is None:
        return False, "MT5 terminal not available (is MetaTrader 5 running?)"
    if not getattr(terminal, "connected", False):
        return False, "MT5 terminal not connected to trade server"

    acc = mt5.account_info()
    if acc is None or getattr(acc, "login", 0) == 0:
        return False, "No active MT5 account session"

    if expected_login is not None and int(acc.login) != int(expected_login):
        return False, (
            f"Logged in as {acc.login}, expected {expected_login} "
            f"(check MT5_LOGIN in .env)"
        )

    return True, ""


def _mark_mt5_connected(acc, execution_mode: str | None = None) -> None:
    with global_state.lock:
        global_state.mt5_initialized = True
        global_state.logged_in = True
        global_state.status = "CONNECTED"
        global_state.error_msg = ""
        if execution_mode is not None:
            global_state.mode = execution_mode
    if acc:
        _apply_mt5_account_to_state(acc)
        with global_state.lock:
            if not global_state.account_name:
                global_state.account_name = getattr(acc, "name", str(acc.login))


def _process_mt5_login(pending: dict, execution_mode: str) -> None:
    """Login to MT5 from the execution thread (required by MetaTrader5 API)."""
    logging.info(f"MT5 login: {pending['login']}@{pending['server']} | mode={execution_mode}")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 terminal init failed: {mt5.last_error()}")

    authorized = mt5.login(
        login=pending["login"],
        password=pending["password"],
        server=pending["server"],
    )
    if not authorized:
        raise RuntimeError(f"MT5 login rejected: {mt5.last_error()}")

    ok, reason = _verify_mt5_connection(pending["login"])
    if not ok:
        raise RuntimeError(f"MT5 login succeeded but connection not confirmed: {reason}")

    for sym in global_state.symbols:
        if mt5.symbol_select(sym, True):
            continue
        logging.warning(f"Symbol '{sym}' not available on this broker.")

    acc = mt5.account_info()
    with global_state.lock:
        global_state.pending_login = None
    _mark_mt5_connected(acc, execution_mode)
    logging.info(
        f"MT5 connection confirmed. Account {pending['login']} | "
        f"Balance ${acc.balance if acc else 0:.2f} | mode={execution_mode.upper()}"
    )


def _queue_env_mt5_login(execution_mode: str) -> bool:
    creds = get_mt5_credentials()
    if creds is None:
        return False
    with global_state.lock:
        if global_state.logged_in or global_state.pending_login is not None:
            return True
        global_state.pending_login = {
            **creds,
            "live": execution_mode == "live",
        }
        global_state.status = "CONNECTING"
        global_state.error_msg = "Logging in from .env..."
    return True


# ─── Execution Thread ────────────────────────────────────────────────────────
def execution_loop(timeframe_str: str, mode: str):
    """
    Single-threaded execution loop that owns ALL MetaTrader5 API calls.
    """
    logging.info("Starting multi-asset execution thread...")

    strategy = get_strategy()
    cfg = get_risk_settings()
    logging.info(
        f"Strategy: {strategy.name} | TF: {timeframe_str} | "
        f"Risk ${cfg['risk_per_trade']:.0f}/trade (quantity from SL; margin/leverage from MT5)"
    )

    with global_state.lock:
        global_state.mode = mode

    risk_mgr = RiskManager(
        max_daily_loss=cfg["max_daily_loss"],
        max_trades_per_day=cfg["max_trades_per_day"],
        max_concurrent_trades=global_state.max_concurrent_trades,
        risk_per_trade_cash=cfg["risk_per_trade"],
        trade_capital_usd=None,
        leverage=100.0,
        symbol="EURUSD",
        symbol_contract_size=get_contract_size("EURUSD"),
    )

    tf_map = {
        'M1': mt5.TIMEFRAME_M1, 'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15, 'M30': mt5.TIMEFRAME_M30,
        'H1': mt5.TIMEFRAME_H1, 'H2': mt5.TIMEFRAME_H2,
        'H4': mt5.TIMEFRAME_H4, 'D1': mt5.TIMEFRAME_D1
    }
    tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_M1)

    last_signal_bar = {sym: None for sym in global_state.symbols}
    current_day = None
    last_scan_log = 0.0
    last_scan_fingerprint = ""
    last_hedge_warn_fp = ""
    last_hedge_audit = 0.0
    scan_log_interval = max(5.0, float(os.getenv("SCAN_LOG_INTERVAL", "60")))
    logging.info(
        f"Scan status log: every {scan_log_interval:.0f}s (or instantly on trade/block/state change). "
        f"Set SCAN_LOG_INTERVAL=10 in .env for more frequent updates."
    )
    logging.info("Anti-hedge ON: no BUY+SELL on the same pair (cross-pair directions allowed)")

    env_creds = get_mt5_credentials()
    if env_creds is None:
        logging.warning(
            "MT5_LOGIN / MT5_PASSWORD / MT5_SERVER not set in .env — "
            "engine will wait until you log in inside the MT5 terminal. "
            "No scanning until connection is confirmed."
        )
    else:
        logging.info(
            f"MT5 auto-login configured (account {os.getenv('MT5_LOGIN')} @ "
            f"{os.getenv('MT5_SERVER')}) | ENGINE_MODE={mode}"
        )
        _queue_env_mt5_login(mode)

    # ── Step 1: Initialize MT5 terminal once ──
    logging.info("Initializing MetaTrader5 terminal...")
    if not mt5.initialize():
        logging.warning(f"MT5 initial startup failed: {mt5.last_error()}. Will retry on credential handoff.")
    else:
        with global_state.lock:
            global_state.mt5_initialized = True
        logging.info("MT5 terminal initialized successfully.")
        if not global_state.logged_in:
            _queue_env_mt5_login(mode)

    # ── Main loop ──
    last_env_login_retry = time.time()
    last_connection_wait_log = 0.0
    mt5_ready_announced = False
    while True:
        try:
            # ── Handle pending login (.env) ──
            with global_state.lock:
                pending = global_state.pending_login
                execution_mode = global_state.mode

            if pending is not None:
                try:
                    _process_mt5_login(pending, execution_mode)
                except Exception as e:
                    logging.error(f"MT5 login failed: {e}")
                    with global_state.lock:
                        global_state.status = "ERROR"
                        global_state.error_msg = str(e)
                        global_state.pending_login = None
                    time.sleep(5)
                    continue

            # ── Handle pending data downloads ──
            with global_state.lock:
                downloads = list(global_state.pending_downloads)
                global_state.pending_downloads = []

            for dl in downloads:
                try:
                    _process_download(dl, tf_map)
                except Exception as e:
                    logging.error(f"Download error for {dl.get('symbol', '?')}: {e}")
                finally:
                    dl["done"].set()

            # ── Check MT5 is alive ──
            with global_state.lock:
                is_init = global_state.mt5_initialized
                is_logged = global_state.logged_in

            if not is_init:
                time.sleep(1)
                continue

            if not is_logged:
                if env_creds is None:
                    _sync_mt5_account_from_terminal()
                    with global_state.lock:
                        is_logged = global_state.logged_in

            if not is_logged:
                if env_creds and (time.time() - last_env_login_retry) >= 30:
                    _queue_env_mt5_login(mode)
                    last_env_login_retry = time.time()
                now_wait = time.time()
                if now_wait - last_connection_wait_log >= 30:
                    with global_state.lock:
                        st = global_state.status
                        err = global_state.error_msg
                    if env_creds:
                        hint = (
                            f"auto-login to {env_creds['login']}@{env_creds['server']}"
                        )
                    else:
                        hint = "log in inside MT5 or set MT5_LOGIN/MT5_PASSWORD/MT5_SERVER in .env"
                    detail = f" ({err})" if err else ""
                    logging.info(f"Waiting for MT5 connection [{st}] — {hint}{detail}")
                    last_connection_wait_log = now_wait
                time.sleep(0.5)
                continue

            expected_login = env_creds["login"] if env_creds else None
            ok, reason = _verify_mt5_connection(expected_login)
            if not ok:
                with global_state.lock:
                    global_state.logged_in = False
                    global_state.status = "DISCONNECTED"
                    global_state.error_msg = reason
                mt5_ready_announced = False
                now_wait = time.time()
                if now_wait - last_connection_wait_log >= 30:
                    logging.warning(f"MT5 not confirmed connected: {reason}")
                    last_connection_wait_log = now_wait
                time.sleep(1)
                continue

            with global_state.lock:
                if global_state.status != "CONNECTED":
                    global_state.status = "CONNECTED"
                    global_state.error_msg = ""
                current_mode = global_state.mode
                symbols_to_scan = global_state.symbols.copy()

            if not mt5_ready_announced:
                acc = mt5.account_info()
                logging.info(
                    f"MT5 ready — scanning enabled. Account {getattr(acc, 'login', '?')} | "
                    f"Balance ${getattr(acc, 'balance', 0):.2f}"
                )
                try:
                    _sync_journal_from_mt5(symbols_to_scan)
                except Exception as e:
                    logging.warning(f"MT5 journal sync skipped: {e}")
                mt5_ready_announced = True

            # ── Daily reset ──
            today = date.today()
            if current_day != today:
                current_day = today
                risk_mgr.reset_daily_metrics()
                with global_state.lock:
                    global_state.pnl_today = 0.0

            # ── Sync account ──
            acc_info = mt5.account_info()
            if acc_info:
                _apply_mt5_account_to_state(acc_info)

            # ── Scan each symbol ──
            with global_state.lock:
                is_logged_scan = global_state.logged_in
            now_audit = time.time()
            if is_logged_scan and (now_audit - last_hedge_audit) >= 60:
                warnings = audit_mt5_book(symbols_to_scan)
                warn_fp = "|".join(warnings)
                if warnings and warn_fp != last_hedge_warn_fp:
                    for warn in warnings:
                        logging.warning(f"MT5 hedge audit: {warn}")
                    last_hedge_warn_fp = warn_fp
                elif not warnings:
                    last_hedge_warn_fp = ""
                last_hedge_audit = now_audit

            scan_results: list[dict] = []
            for sym in symbols_to_scan:
                try:
                    result = _scan_symbol(
                        sym, tf, strategy, risk_mgr,
                        current_mode, last_signal_bar
                    )
                    if result:
                        scan_results.append(result)
                except Exception as e:
                    logging.error(f"Error scanning {sym}: {e}", exc_info=True)

            now = time.time()
            if _should_log_scan(scan_results, last_scan_fingerprint, last_scan_log, scan_log_interval):
                _log_scan_round(scan_results, timeframe_str, current_mode)
                last_scan_log = now
                last_scan_fingerprint = _scan_fingerprint(scan_results)

        except Exception as e:
            logging.error(f"Execution loop error: {e}", exc_info=True)

        time.sleep(0.1)


# ─── MT5 account helpers ─────────────────────────────────────────────────────
def _apply_mt5_account_to_state(acc) -> None:
    if acc is None:
        return
    with global_state.lock:
        global_state.balance = float(acc.balance)
        global_state.equity = float(acc.equity)
        global_state.margin_free = float(getattr(acc, "margin_free", 0) or 0)
        lev = int(getattr(acc, "leverage", 0) or 0)
        global_state.account_leverage = lev
        if getattr(acc, "name", None):
            global_state.account_name = acc.name


def _margin_budget_per_trade(active_positions: int, max_concurrent: int, acc) -> float | None:
    """Share of free margin available for the next position."""
    if acc is None or getattr(acc, "login", 0) == 0:
        return None
    margin_free = float(getattr(acc, "margin_free", 0) or 0)
    if margin_free <= 0:
        margin_free = float(getattr(acc, "equity", 0) or getattr(acc, "balance", 0) or 0)
    slots = max(1, max_concurrent - active_positions)
    return margin_free / slots


# ─── Data Download (runs inside execution thread) ────────────────────────────
def _sync_mt5_account_from_terminal() -> bool:
    """Pick up an account already logged in inside the MT5 terminal GUI (no .env creds)."""
    if get_mt5_credentials() is not None:
        return False

    ok, reason = _verify_mt5_connection()
    if not ok:
        return False

    acc = mt5.account_info()
    if acc is None or acc.login == 0:
        return False

    _mark_mt5_connected(acc)
    logging.info(
        f"MT5 connection confirmed (terminal GUI). Account: {acc.login}, "
        f"Balance: ${acc.balance:.2f}, Free margin: ${getattr(acc, 'margin_free', 0):.2f}, "
        f"Leverage 1:{getattr(acc, 'leverage', 0)}"
    )
    return True


def _process_download(dl: dict, tf_map: dict):
    """Download historical data from MT5 and save to CSV. Called from execution thread."""
    symbol = dl["symbol"]
    timeframe_str = dl["timeframe"]
    bars = dl["bars"]
    csv_path = dl["csv_path"]

    acc = mt5.account_info()
    if acc is None or acc.login == 0:
        dl["error"] = "MT5 account not logged in. Use Connect MT5 on the Live tab, then retry."
        return

    tf = tf_map.get(timeframe_str)
    if tf is None:
        dl["error"] = f"Invalid timeframe '{timeframe_str}'."
        return

    if not mt5.symbol_select(symbol, True):
        dl["error"] = f"Symbol '{symbol}' is not available on this broker account."
        return

    logging.info(f"Downloading {bars} bars for {symbol} {timeframe_str}...")
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)

    if rates is None or len(rates) == 0:
        dl["error"] = (
            f"No candle history for {symbol} {timeframe_str}. "
            f"Open a {symbol} {timeframe_str} chart in MT5, scroll back to load bars, then retry."
        )
        logging.warning(dl["error"])
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread']]
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    df.to_csv(csv_path, index=False)
    dl["error"] = None
    logging.info(f"Saved {len(df)} bars -> {csv_path}")


# ─── Scan console logging ─────────────────────────────────────────────────────
def _step_mark(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


def _scan_wait_reason(steps: dict, signal_action: str) -> str:
    if not steps.get("atr_ok"):
        return "step 1 FAIL — ATR not in normal range"
    if not steps.get("band_signal"):
        return "step 2 WAIT — no +/-2s band cross on last closed bar"
    if signal_action in ("BUY", "SELL") and not steps.get("stops_set"):
        return "step 3 FAIL — could not set SL/TP levels"
    if signal_action in ("BUY", "SELL"):
        return f"step 3 OK — {signal_action} signal ready"
    return "scanning — no setup"


def _scan_fingerprint(results: list[dict]) -> str:
    parts: list[str] = []
    for r in sorted(results, key=lambda x: x.get("symbol", "")):
        steps = r.get("steps") or {}
        parts.append(
            f"{r.get('symbol')}:{r.get('status')}:{r.get('signal_action')}:"
            f"{int(steps.get('atr_ok', False))}{int(steps.get('band_signal', False))}{int(steps.get('stops_set', False))}"
        )
    return "|".join(parts)


def _should_log_scan(results: list[dict], last_fp: str, last_log_ts: float, interval: float) -> bool:
    if not results:
        return False
    if any(r.get("status") in ("TRADE", "BLOCKED") for r in results):
        return True
    if _scan_fingerprint(results) != last_fp:
        return True
    return (time.time() - last_log_ts) >= interval


def _log_scan_round(results: list[dict], timeframe: str, mode: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    logging.info(f"---------- SCAN {timeframe} | {mode.upper()} | {ts} | {len(results)} pairs ----------")
    for r in results:
        sym = r.get("symbol", "?")
        price = r.get("price")
        price_s = f"{price:.5f}" if price else "—"
        steps = r.get("steps") or {}
        s1 = _step_mark(steps.get("atr_ok", False))
        s2 = _step_mark(steps.get("band_signal", False))
        s3 = _step_mark(steps.get("stops_set", False))
        pipe = f"[1]ATR:{s1} [2]BAND:{s2} [3]STOPS:{s3}"

        status = r.get("status", "WAIT")
        if status == "IN_POSITION":
            action = r.get("action", "?")
            pnl = r.get("pnl", 0.0)
            label = "LIVE TRADE" if mode == "live" else "DRY-RUN SIM (not on MT5)"
            logging.info(f"  {sym:7s} {price_s} | {pipe} | {label} {action} | float ${pnl:+.2f}")
        elif status == "TRADE":
            logging.info(
                f"  {sym:7s} {price_s} | {pipe} | >>> TRADE {r.get('action')} "
                f"{r.get('lot', 0):.2f} lots @ {r.get('entry', 0):.5f}"
            )
        elif status == "BLOCKED":
            logging.info(f"  {sym:7s} {price_s} | {pipe} | BLOCKED — {r.get('reason', '')}")
        elif status == "ACTED":
            logging.info(f"  {sym:7s} {price_s} | {pipe} | SKIP — already processed this M15 bar (no re-entry)")
        elif status == "FAILED":
            logging.info(f"  {sym:7s} {price_s} | {pipe} | ORDER FAILED — {r.get('reason', 'MT5 rejected')}")
        elif status == "SKIP":
            logging.info(f"  {sym:7s} — | SKIP — {r.get('reason', '')}")
        else:
            reason = r.get("reason") or _scan_wait_reason(steps, r.get("signal_action", "HOLD"))
            logging.info(f"  {sym:7s} {price_s} | {pipe} | {reason}")
    logging.info("---------- end scan ----------")


# ─── Symbol Scanner ──────────────────────────────────────────────────────────
def _inject_live_tick(df: pd.DataFrame, sym: str) -> float | None:
    """Merge latest MT5 tick into the forming bar so bands update every scan."""
    tick = mt5.symbol_info_tick(sym)
    if tick is None or tick.bid <= 0:
        return None
    price = (float(tick.bid) + float(tick.ask)) / 2.0
    last = df.index[-1]
    df.at[last, "close"] = price
    df.at[last, "high"] = max(float(df.at[last, "high"]), price)
    df.at[last, "low"] = min(float(df.at[last, "low"]), price)
    return price


def _scan_symbol(
    sym: str, tf: int,
    strategy: BaseStrategy, risk_mgr: RiskManager,
    current_mode: str,
    last_signal_bar: dict,
) -> dict | None:
    """Process a single symbol: fetch bars, check positions, detect signals, execute."""

    if not mt5.symbol_select(sym, True):
        return {"symbol": sym, "status": "SKIP", "reason": "symbol not on broker"}

    bars_needed = strategy.warmup_bars() + 10
    rates = mt5.copy_rates_from_pos(sym, tf, 0, bars_needed)
    if rates is None or len(rates) < strategy.warmup_bars() + 2:
        return {"symbol": sym, "status": "SKIP", "reason": "not enough M15 history"}

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    live_price = _inject_live_tick(df, sym)
    df = strategy.prepare(df)

    current_bar = df.iloc[-1]
    closed_bar = df.iloc[-2] if len(df) >= 2 else None
    lookback = strategy.lookback_bars()
    history = df.iloc[max(0, len(df) - lookback - 1) : -1]
    if len(history) < 1:
        return {"symbol": sym, "status": "SKIP", "reason": "warmup history"}

    signal_bar = history.iloc[-1]
    contract_size = get_contract_size(sym)
    cfg = get_risk_settings()

    with global_state.lock:
        global_state.active_swing_highs[sym] = float(current_bar.get("upper", 0) or 0)
        global_state.active_swing_lows[sym] = float(current_bar.get("lower", 0) or 0)
        global_state.active_poc[sym] = float(current_bar.get("ma25", 0) or 0)
        if live_price is not None:
            global_state.last_prices[sym] = live_price
        global_state.bands_updated_at[sym] = time.time()

    if current_mode == "live":
        _manage_live_position(sym, current_bar, risk_mgr, contract_size)
    else:
        _manage_dry_position(sym, closed_bar, current_bar, risk_mgr, contract_size)

    signal = strategy.evaluate(current_bar, history)
    steps = {
        "atr_ok": bool(signal.rules.get("atr_ok", False)),
        "band_signal": bool(signal.rules.get("band_signal", False)),
        "stops_set": bool(signal.rules.get("stops_set", False)),
    }

    acc = mt5.account_info()
    with global_state.lock:
        global_state.rules[sym] = signal.rules
        active_count = sum(1 for p in global_state.active_positions.values() if p is not None)
        risk_mgr.active_positions_count = active_count
        risk_mgr.risk_per_trade_cash = cfg["risk_per_trade"]
        risk_mgr.symbol = sym
        risk_mgr.symbol_contract_size = contract_size
        if acc and getattr(acc, "login", 0):
            risk_mgr.leverage = float(getattr(acc, "leverage", 0) or 100)
            risk_mgr.trade_capital_usd = _margin_budget_per_trade(
                active_count, cfg["max_concurrent_trades"], acc
            )
        else:
            risk_mgr.trade_capital_usd = None
            risk_mgr.leverage = 100.0
        has_position = global_state.active_positions.get(sym) is not None
        open_pos = global_state.active_positions.get(sym)

    base = {
        "symbol": sym,
        "price": live_price,
        "steps": steps,
        "signal_action": signal.action,
    }

    if has_position and open_pos:
        return {
            **base,
            "status": "IN_POSITION",
            "action": open_pos.get("action"),
            "pnl": float(open_pos.get("net_pnl", 0) or 0),
        }

    signal_time = signal_bar["time"]
    allowed, block_reason = risk_mgr.is_trading_allowed()

    if signal.action in ("BUY", "SELL"):
        if not allowed:
            return {**base, "status": "BLOCKED", "action": signal.action, "reason": block_reason}
        with global_state.lock:
            positions_snapshot = dict(global_state.active_positions)
            mt5_logged = global_state.logged_in
        hedge_ok, hedge_reason = validate_new_entry(
            sym, signal.action, positions_snapshot, check_mt5=mt5_logged,
        )
        if not hedge_ok:
            return {**base, "status": "BLOCKED", "action": signal.action, "reason": hedge_reason}
        if last_signal_bar.get(sym) == signal_time:
            return {
                **base,
                "status": "ACTED",
                "action": signal.action,
                "reason": "already processed this M15 bar",
            }

        entry_price = (
            float(current_bar["open"])
            if getattr(strategy, "entry_at_open", False)
            else float(current_bar["close"])
        )
        lot_size = risk_mgr.calculate_lot_size(entry_price=entry_price, sl_price=float(signal.sl))
        if lot_size <= 0:
            return {**base, "status": "SKIP", "reason": "lot size 0 (margin or SL too tight)"}

        signal_payload = signal.to_dict()
        logging.info(
            f">>> ALL STEPS PASS -> PLACE {signal.action} {sym} @ {entry_price:.5f} | "
            f"lot {lot_size:.2f} | {signal.reason}"
        )
        opened = False
        if current_mode == "live":
            opened = _execute_live_order(sym, signal_payload, lot_size, risk_mgr)
        else:
            _execute_dry_order(sym, signal_payload, lot_size, current_bar, risk_mgr, strategy)
            opened = True

        if not opened:
            return {**base, "status": "FAILED", "action": signal.action, "reason": "MT5 order rejected"}

        last_signal_bar[sym] = signal_time
        return {
            **base,
            "status": "TRADE",
            "action": signal.action,
            "lot": lot_size,
            "entry": entry_price,
        }

    return {
        **base,
        "status": "WAIT",
        "reason": _scan_wait_reason(steps, signal.action),
    }


# ─── Live exit resolution (MT5 deal history) ────────────────────────────────
def _bar_time_str(bar_time) -> str:
    return bar_time_to_str(bar_time)


def _deal_reason_label(reason: int) -> str:
    mapping = {
        getattr(mt5, "DEAL_REASON_SL", 4): "STOP LOSS HIT",
        getattr(mt5, "DEAL_REASON_TP", 5): "MEAN TARGET HIT",
        getattr(mt5, "DEAL_REASON_SO", 6): "STOP OUT",
        getattr(mt5, "DEAL_REASON_EXPERT", 3): "EXPERT CLOSE",
        getattr(mt5, "DEAL_REASON_CLIENT", 0): "MANUAL CLOSE",
    }
    return mapping.get(reason, "CLOSED")


def _fetch_mt5_deals(
    from_time: datetime,
    to_time: datetime,
    *,
    position_id: int | None = None,
    ticket: int | None = None,
) -> list:
    """Fetch MT5 deal history (this Python build has no history_deals_select)."""
    if position_id is not None:
        deals = mt5.history_deals_get(position=position_id)
    elif ticket is not None:
        deals = mt5.history_deals_get(ticket=ticket)
    else:
        deals = mt5.history_deals_get(from_time, to_time)
    if deals is None:
        err = mt5.last_error()
        if err and err[0] != 1:
            logging.debug(f"history_deals_get: {err}")
        return []
    return list(deals)


def _sync_journal_from_mt5(symbols: list[str], days: int = 90) -> int:
    """Import closed round-trip trades from the connected MT5 account."""
    from_time = datetime.now() - timedelta(days=days)
    to_time = datetime.now() + timedelta(minutes=1)
    deals = _fetch_mt5_deals(from_time, to_time)
    if not deals:
        return 0

    sym_set = {s.upper() for s in symbols}
    deal_entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
    deal_entry_out = getattr(mt5, "DEAL_ENTRY_OUT", 1)
    deal_type_buy = getattr(mt5, "DEAL_TYPE_BUY", 0)

    by_pos: dict[int, list] = {}
    for deal in deals:
        if deal.symbol.upper() not in sym_set:
            continue
        by_pos.setdefault(deal.position_id, []).append(deal)

    with global_state.lock:
        existing_pids = {t.get("position_id") for t in global_state.trades if t.get("position_id")}

    added = 0
    for pid, pos_deals in by_pos.items():
        if pid in existing_pids:
            continue
        in_deals = [d for d in pos_deals if getattr(d, "entry", None) == deal_entry_in]
        out_deals = [d for d in pos_deals if getattr(d, "entry", None) == deal_entry_out]
        if not in_deals or not out_deals:
            continue
        in_d = sorted(in_deals, key=lambda d: d.time)[0]
        out_d = sorted(out_deals, key=lambda d: d.time)[-1]
        action = "BUY" if in_d.type == deal_type_buy else "SELL"
        reason = _deal_reason_label(getattr(out_d, "reason", -1))
        entry_dt = mt5_ts_to_datetime(in_d.time)
        exit_dt = mt5_ts_to_datetime(out_d.time)
        net_pnl = sum(float(d.profit) for d in pos_deals)

        trade = {
            "symbol": in_d.symbol,
            "mode": "live",
            "timeframe": global_state.engine_timeframe,
            "strategy": global_state.strategy_name,
            "entry_time": mt5_ts_to_str(in_d.time),
            "exit_time": mt5_ts_to_str(out_d.time),
            "action": action,
            "entry_price": float(in_d.price),
            "exit_price": float(out_d.price),
            "size": float(in_d.volume),
            "sl": 0.0,
            "tp": 0.0,
            "net_pnl": net_pnl,
            "commission": sum(float(getattr(d, "commission", 0) or 0) for d in pos_deals),
            "swap": sum(float(getattr(d, "swap", 0) or 0) for d in pos_deals),
            "result": reason,
            "exit_type": "SL" if "STOP" in reason else ("TP" if "TARGET" in reason else "OTHER"),
            "position_id": pid,
            "ticket": out_d.ticket,
            "reason": getattr(in_d, "comment", "") or "MT5 account",
        }
        save_trade_to_history(trade)
        added += 1

    if added:
        logging.info(f"Journal synced {added} closed trade(s) from MT5 account.")
    return added


def _resolve_live_exit(sym: str, prev: dict) -> dict | None:
    """Read MT5 deal history for accurate exit time, price, P&L, and reason."""
    position_id = prev.get("position_id")
    ticket = prev.get("ticket")

    if position_id:
        deals = _fetch_mt5_deals(datetime.now(), datetime.now(), position_id=position_id)
    else:
        entry_dt = parse_trade_time(prev.get("entry_time"))
        from_time = entry_dt or (datetime.now() - timedelta(days=30))
        to_time = datetime.now() + timedelta(minutes=1)
        deals = _fetch_mt5_deals(from_time, to_time)
    if not deals:
        return None

    out_deals = []
    for deal in deals:
        if deal.symbol != sym:
            continue
        if getattr(deal, "entry", None) != getattr(mt5, "DEAL_ENTRY_OUT", 1):
            continue
        if position_id and deal.position_id == position_id:
            out_deals.append(deal)
        elif ticket and deal.position_id == ticket:
            out_deals.append(deal)

    if not out_deals and ticket:
        ticket_deals = _fetch_mt5_deals(datetime.now(), datetime.now(), ticket=ticket)
        for deal in ticket_deals:
            if deal.symbol == sym:
                out_deals.append(deal)

    if not out_deals:
        return None

    deal = sorted(out_deals, key=lambda d: d.time)[-1]
    reason = _deal_reason_label(getattr(deal, "reason", -1))
    return {
        "exit_time": mt5_ts_to_str(deal.time),
        "exit_price": float(deal.price),
        "net_pnl": float(deal.profit),
        "result": reason,
        "exit_type": "SL" if "STOP" in reason else ("TP" if "TARGET" in reason else "OTHER"),
        "commission": float(getattr(deal, "commission", 0) or 0),
        "swap": float(getattr(deal, "swap", 0) or 0),
    }


def _build_closed_trade(sym: str, prev: dict, *, fallback_bar=None, mode: str | None = None) -> dict:
    mode = mode or prev.get("mode") or global_state.mode
    exit_info = None
    if mode == "live":
        exit_info = _resolve_live_exit(sym, prev)

    if exit_info:
        exit_time = exit_info["exit_time"]
        exit_price = exit_info["exit_price"]
        net_pnl = exit_info["net_pnl"]
        result = exit_info["result"]
        exit_type = exit_info.get("exit_type")
    else:
        exit_time = _bar_time_str(fallback_bar.get("time") if fallback_bar is not None else None)
        exit_price = float(fallback_bar["close"]) if fallback_bar is not None else float(prev.get("entry_price", 0))
        net_pnl = float(prev.get("net_pnl", 0.0))
        result = prev.get("pending_result", "CLOSED")
        exit_type = None

    trade = {
        "symbol": sym,
        "mode": mode,
        "timeframe": global_state.engine_timeframe,
        "strategy": global_state.strategy_name,
        "entry_time": prev.get("entry_time"),
        "action": prev.get("action"),
        "entry_price": prev.get("entry_price"),
        "size": prev.get("size"),
        "sl": prev.get("sl"),
        "tp": prev.get("tp"),
        "exit_time": exit_time,
        "exit_price": exit_price,
        "net_pnl": net_pnl,
        "result": result,
        "exit_type": exit_type,
        "reason": prev.get("reason", ""),
        "bars_held": prev.get("bars_held", 0),
        "ticket": prev.get("ticket"),
        "position_id": prev.get("position_id"),
    }
    return enrich_trade_record(trade, default_timeframe=global_state.engine_timeframe)


# ─── Position Management ────────────────────────────────────────────────────
def _manage_live_position(sym: str, current_bar, risk_mgr: RiskManager, contract_size: float):
    """Track live MT5 positions for a symbol."""
    positions = mt5.positions_get(symbol=sym)
    bar_time = current_bar.get("time")
    closed_prev = None

    with global_state.lock:
        if positions and len(positions) > 0:
            pos = positions[0]
            existing = global_state.active_positions.get(sym) or {}
            bars_held = existing.get("bars_held", 0)
            if existing.get("last_bar_time") != bar_time:
                bars_held += 1 if existing.get("last_bar_time") is not None else 0

            entry_ts = existing.get("entry_time")
            if not entry_ts:
                entry_ts = mt5_ts_to_str(pos.time)

            elapsed = ""
            entry_dt = parse_trade_time(entry_ts)
            if entry_dt:
                elapsed = format_duration((datetime.now() - entry_dt).total_seconds())

            global_state.active_positions[sym] = {
                "action": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                "entry_price": pos.price_open,
                "entry_time": entry_ts,
                "size": pos.volume,
                "sl": pos.sl,
                "tp": pos.tp,
                "net_pnl": pos.profit,
                "symbol": sym,
                "mode": "live",
                "ticket": pos.ticket,
                "position_id": pos.identifier,
                "bars_held": bars_held,
                "last_bar_time": bar_time,
                "elapsed_label": elapsed,
                "reason": existing.get("reason", ""),
            }
        else:
            prev = global_state.active_positions.get(sym)
            if prev is not None:
                closed_prev = dict(prev)
                global_state.active_positions[sym] = None

    if closed_prev is not None:
        logging.info(f"Live position {sym} closed — recording to journal.")
        closed_trade = _build_closed_trade(sym, closed_prev, fallback_bar=current_bar, mode="live")
        save_trade_to_history(closed_trade)
        risk_mgr.log_trade_close(closed_trade.get("net_pnl", 0.0))
        with global_state.lock:
            global_state.pnl_today += float(closed_trade.get("net_pnl", 0.0))


def _manage_dry_position(
    sym: str,
    closed_bar,
    forming_bar,
    risk_mgr: RiskManager,
    contract_size: float,
):
    """Simulate position management — exits on closed M15 bars only (matches backtester)."""
    with global_state.lock:
        active_pos = global_state.active_positions.get(sym)
        if active_pos is not None:
            active_pos = dict(active_pos)

    if active_pos is None:
        return

    is_closed = False
    exit_price = 0.0
    exit_reason = ""

    # Floating P&L from the live forming bar
    if forming_bar is not None:
        floating_pnl = calc_gross_pnl_usd(
            sym, active_pos["action"], active_pos["entry_price"],
            float(forming_bar["close"]), active_pos["size"], contract_size,
        )
        entry_dt = parse_trade_time(active_pos.get("entry_time"))
        elapsed = format_duration((datetime.now() - entry_dt).total_seconds()) if entry_dt else "—"
        with global_state.lock:
            if global_state.active_positions.get(sym) is not None:
                global_state.active_positions[sym]["net_pnl"] = floating_pnl
                global_state.active_positions[sym]["elapsed_label"] = elapsed

    if closed_bar is None:
        return

    closed_time = closed_bar.get("time")
    entry_bar_time = active_pos.get("entry_bar_time")
    if entry_bar_time is not None and closed_time < entry_bar_time:
        return

    if active_pos.get("last_exit_check_bar_time") == closed_time:
        return

    active_pos["last_exit_check_bar_time"] = closed_time
    active_pos["bars_held"] = active_pos.get("bars_held", 0) + 1

    with global_state.lock:
        if global_state.active_positions.get(sym) is not None:
            global_state.active_positions[sym]["bars_held"] = active_pos["bars_held"]
            global_state.active_positions[sym]["last_exit_check_bar_time"] = closed_time

    max_hold = active_pos.get("max_hold_bars")
    if max_hold and active_pos.get("bars_held", 0) >= max_hold:
        is_closed, exit_price, exit_reason = True, float(closed_bar["close"]), "TIME EXIT"

    if not is_closed and active_pos["action"] == "BUY":
        tp_trig = active_pos.get("tp_trigger", active_pos["tp"])
        if closed_bar["low"] <= active_pos["sl"]:
            is_closed, exit_price, exit_reason = True, active_pos["sl"], "STOP LOSS HIT"
        elif closed_bar["high"] >= tp_trig:
            is_closed, exit_price, exit_reason = (
                True, min(float(closed_bar["high"]), active_pos["tp"]), "MEAN TARGET HIT",
            )
    elif not is_closed and active_pos["action"] == "SELL":
        tp_trig = active_pos.get("tp_trigger", active_pos["tp"])
        if closed_bar["high"] >= active_pos["sl"]:
            is_closed, exit_price, exit_reason = True, active_pos["sl"], "STOP LOSS HIT"
        elif closed_bar["low"] <= tp_trig:
            is_closed, exit_price, exit_reason = (
                True, max(float(closed_bar["low"]), active_pos["tp"]), "MEAN TARGET HIT",
            )

    if is_closed:
        gross_pnl = calc_gross_pnl_usd(
            sym, active_pos["action"], active_pos["entry_price"],
            exit_price, active_pos["size"], contract_size,
        )
        tick_val = tick_value_usd(sym, exit_price, contract_size)
        comm = active_pos["size"] * float(os.getenv("COMMISSION_PER_LOT", 7.0))
        slippage = (float(os.getenv("SLIPPAGE_TICKS", 2.0)) * 2) * tick_val * active_pos["size"]
        net_pnl = gross_pnl - comm - slippage

        closed_trade = {
            "symbol": sym,
            "mode": global_state.mode,
            "timeframe": global_state.engine_timeframe,
            "strategy": global_state.strategy_name,
            "entry_time": active_pos["entry_time"],
            "action": active_pos["action"],
            "entry_price": active_pos["entry_price"],
            "size": active_pos["size"],
            "sl": active_pos["sl"],
            "tp": active_pos["tp"],
            "exit_time": _bar_time_str(closed_time),
            "exit_price": exit_price,
            "net_pnl": net_pnl,
            "result": exit_reason,
            "reason": active_pos.get("reason", ""),
            "bars_held": active_pos.get("bars_held", 0),
        }
        save_trade_to_history(closed_trade)
        risk_mgr.log_trade_close(net_pnl)

        with global_state.lock:
            global_state.active_positions[sym] = None
            global_state.pnl_today += net_pnl

        logging.info(
            f"Dry-run {sym} closed: {exit_reason} | "
            f"held {active_pos.get('bars_held', 0)} bars | Net P&L: ${net_pnl:.2f}"
        )


# ─── Order Execution ────────────────────────────────────────────────────────
def _filling_modes_for_symbol(sym: str) -> list[int]:
    """Broker-supported filling modes for this symbol (IOC often fails on FX)."""
    info = mt5.symbol_info(sym)
    modes: list[int] = []
    if info is not None:
        filling = int(getattr(info, "filling_mode", 0) or 0)
        if filling & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            modes.append(mt5.ORDER_FILLING_IOC)
        if filling & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            modes.append(mt5.ORDER_FILLING_FOK)
    for fallback in (
        mt5.ORDER_FILLING_RETURN,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
    ):
        if fallback not in modes:
            modes.append(fallback)
    return modes


def _execute_live_order(sym: str, signal: dict, lot_size: float, risk_mgr: RiskManager) -> bool:
    """Send a real order to MT5. Returns True if filled."""
    tick = mt5.symbol_info_tick(sym)
    if not tick:
        logging.error(f"Cannot get tick for {sym}")
        return False

    order_type = mt5.ORDER_TYPE_BUY if signal['action'] == 'BUY' else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal['action'] == 'BUY' else tick.bid

    base_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": signal['sl'],
        "tp": signal['tp'],
        "deviation": 20,
        "magic": 987654,
        "comment": "DynamicOracle holygrail",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    logging.info(f"LIVE ORDER -> {sym}: {signal['action']} {lot_size} lots @ {price}")
    result = None
    for filling in _filling_modes_for_symbol(sym):
        request = {**base_request, "type_filling": filling}
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            break
        if result and result.retcode == getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030):
            continue
        break

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logging.info(f"Order filled for {sym}.")
        risk_mgr.log_trade_open()
        time.sleep(0.2)
        positions = mt5.positions_get(symbol=sym)
        pos = None
        if positions:
            for p in positions:
                if p.magic == 987654:
                    pos = p
                    break
            if pos is None:
                pos = positions[0]
        if pos:
            with global_state.lock:
                global_state.active_positions[sym] = {
                    "entry_time": mt5_ts_to_str(pos.time),
                    "action": signal["action"],
                    "entry_price": pos.price_open,
                    "size": pos.volume,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "net_pnl": pos.profit,
                    "symbol": sym,
                    "mode": "live",
                    "ticket": pos.ticket,
                    "position_id": pos.identifier,
                    "bars_held": 0,
                    "last_bar_time": None,
                    "reason": signal.get("reason", ""),
                }
        return True

    retcode = result.retcode if result else "None"
    comment = result.comment if result else "No result"
    logging.error(f"Order failed for {sym}. Code: {retcode} | {comment}")
    return False


def _execute_dry_order(sym: str, signal: dict, lot_size: float, current_bar, risk_mgr: RiskManager, strategy: BaseStrategy):
    """Simulate order entry in dry-run mode."""
    entry_price = (
        float(current_bar["open"])
        if getattr(strategy, "entry_at_open", False)
        else float(current_bar["close"])
    )
    entry_time = _bar_time_str(current_bar.get("time"))
    with global_state.lock:
        global_state.active_positions[sym] = {
            "entry_time": entry_time,
            "entry_bar_time": current_bar.get("time"),
            "action": signal['action'],
            "entry_price": entry_price,
            "size": lot_size,
            "sl": float(signal['sl']),
            "tp": float(signal['tp']),
            "tp_trigger": float((signal.get("meta") or {}).get("tp_trigger", signal['tp'])),
            "trigger_level": float(signal.get('trigger_level', 0.0)),
            "max_hold_bars": (signal.get("meta") or {}).get("max_hold_bars"),
            "bars_held": 0,
            "last_exit_check_bar_time": None,
            "max_floating": 0.0,
            "net_pnl": 0.0,
            "reason": signal['reason'],
            "symbol": sym,
            "mode": global_state.mode,
            "timeframe": global_state.engine_timeframe,
            "strategy": global_state.strategy_name,
            "elapsed_label": "0m",
        }
    risk_mgr.log_trade_open()
    logging.info(f"DRY ORDER -> {sym}: {signal['action']} {lot_size} lots @ {entry_price:.5f}")


# ─── Entrypoint ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dynamic Oracle Engine")
    parser.add_argument("--timeframe", type=str, default=None, help="Timeframe (default: ENGINE_TIMEFRAME from .env)")
    default_mode = get_engine_mode()
    parser.add_argument("--mode", type=str, default=default_mode, choices=["dry_run", "live"])
    parser.add_argument("--port", type=int, default=int(os.getenv("ENGINE_PORT", "8000")))

    args = parser.parse_args()
    _chrome_blocked_ports = {
        6000, 6665, 6666, 6667, 6668, 6669, 6697,
    }
    if args.port in _chrome_blocked_ports:
        logging.warning(
            f"Port {args.port} is blocked by Chrome (ERR_UNSAFE_PORT). "
            f"Use 8000 or 8080 instead: python engine.py --port 8000"
        )
    cfg = get_risk_settings()
    timeframe = (args.timeframe or cfg["timeframe"] or "M15").upper()
    with global_state.lock:
        global_state.engine_timeframe = timeframe
        global_state.risk_settings = cfg
        global_state.mode = args.mode

    load_history()

    exec_thread = threading.Thread(
        target=execution_loop,
        args=(timeframe, args.mode),
        name="AlgoExecutionThread",
        daemon=True
    )
    exec_thread.start()

    logging.info(f"Dashboard: http://localhost:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
