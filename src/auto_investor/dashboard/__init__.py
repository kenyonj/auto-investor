"""Web dashboard for auto-investor."""

import asyncio
import json
import os
import sqlite3
import threading
import time
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_VERSION = pkg_version("auto-investor")

app = FastAPI(title="auto-investor dashboard")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

DB_PATH = os.environ.get("DB_PATH", "auto_investor.db")

_first_cycle_at: float | None = None
_alpaca_client = None
_run_cycle_fn = None
_ws_clients: list[WebSocket] = []
_hold_all: bool = False
_pause_ai: bool = False


def set_first_cycle_time(t: float | None) -> None:
    global _first_cycle_at
    _first_cycle_at = t


def set_alpaca_client(client) -> None:
    global _alpaca_client
    _alpaca_client = client


def set_run_cycle_fn(fn) -> None:
    global _run_cycle_fn
    _run_cycle_fn = fn


async def broadcast(msg: dict) -> None:
    """Send a message to all connected WebSocket clients."""
    data = json.dumps(msg)
    stale = []
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _ws_clients.remove(ws)


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_live_pl() -> dict | None:
    if _alpaca_client is None:
        return None
    try:
        account = _alpaca_client.get_account()
        raw_positions = _alpaca_client.trading.get_all_positions()

        def _is_crypto(p) -> bool:
            return getattr(p.asset_class, "value", "us_equity") == "crypto"

        equity_pl = sum(float(p.unrealized_intraday_pl) for p in raw_positions if not _is_crypto(p))
        crypto_pl = sum(float(p.unrealized_intraday_pl) for p in raw_positions if _is_crypto(p))
        equity_mv = sum(float(p.market_value) for p in raw_positions if not _is_crypto(p))
        crypto_mv = sum(float(p.market_value) for p in raw_positions if _is_crypto(p))
        return {
            "equity": account["equity"],
            "cash": account["cash"],
            "daily_pl": equity_pl + crypto_pl,
            "equity_pl": equity_pl,
            "crypto_pl": crypto_pl,
            "buying_power": account["buying_power"],
            "equity_mv": equity_mv,
            "crypto_mv": crypto_mv,
        }
    except Exception:
        return None


def _get_live_positions() -> list[dict] | None:
    if _alpaca_client is None:
        return None
    try:
        positions = _alpaca_client.get_positions()
        return [
            {
                "symbol": _alpaca_client.normalize_symbol(p.symbol),
                "quantity": p.quantity,
                "avg_entry_price": p.avg_entry_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_pl_pct": p.unrealized_pl_pct,
                "asset_class": p.asset_class,
            }
            for p in positions
        ]
    except Exception:
        return None


def _get_decision_count() -> int:
    try:
        db = _get_db()
        row = db.execute("SELECT COUNT(*) as cnt FROM decisions").fetchone()
        db.close()
        return row["cnt"] if row else 0
    except Exception:
        return -1


def _enrich_executions(executions: list[dict]) -> list[dict]:
    """Enrich execution dicts with live fill data and transaction amounts."""
    if _alpaca_client:
        update_db = _get_db()
        for e in executions:
            oid = e.get("order_id")
            if not oid:
                continue
            if e.get("filled_avg_price") and e.get("filled_qty"):
                continue
            try:
                info = _alpaca_client.get_order_details(oid)
                if info:
                    if info.get("status"):
                        e["status"] = info["status"]
                    if info.get("filled_avg_price"):
                        e["filled_avg_price"] = info["filled_avg_price"]
                    if info.get("filled_qty"):
                        e["filled_qty"] = info["filled_qty"]
                    if info.get("filled_avg_price") and info.get("filled_qty"):
                        update_db.execute(
                            "UPDATE executions SET status=?, filled_avg_price=?,"
                            " filled_qty=? WHERE order_id=?",
                            (info["status"], info["filled_avg_price"],
                             info["filled_qty"], oid),
                        )
                        update_db.commit()
            except Exception:
                pass
        update_db.close()

    unfilled_symbols = {
        e["symbol"] for e in executions
        if not (e.get("filled_avg_price") and e.get("filled_qty"))
        and e.get("quantity") and e.get("symbol")
    }
    quote_prices: dict[str, float] = {}
    if unfilled_symbols and _alpaca_client:
        try:
            quotes = _alpaca_client.get_quotes(list(unfilled_symbols))
            for q in quotes:
                quote_prices[q.symbol] = q.price
        except Exception:
            pass
    for e in executions:
        price = e.get("filled_avg_price")
        qty = e.get("filled_qty")
        if price and qty:
            e["txn_amount"] = float(price) * float(qty)
            e["txn_estimated"] = False
        elif e.get("quantity") and e.get("symbol"):
            qp = quote_prices.get(e["symbol"])
            if qp:
                e["txn_amount"] = float(e["quantity"]) * qp
                e["txn_estimated"] = True
            else:
                e["txn_amount"] = None
                e["txn_estimated"] = False
        else:
            e["txn_amount"] = None
            e["txn_estimated"] = False
    return executions


@app.get("/api/decisions")
def api_decisions(request: Request):
    """JSON endpoint for paginated decisions."""
    db = _get_db()
    per_page = max(1, min(int(request.query_params.get("per_page", 10)), 100))
    page = max(1, int(request.query_params.get("page", 1)))
    symbol = request.query_params.get("symbol", "").strip().upper()
    offset = (page - 1) * per_page
    if symbol:
        total = db.execute(
            "SELECT COUNT(*) as cnt FROM decisions WHERE UPPER(symbol) LIKE ?",
            (f"%{symbol}%",),
        ).fetchone()["cnt"]
        rows = db.execute(
            "SELECT * FROM decisions WHERE UPPER(symbol) LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (f"%{symbol}%", per_page, offset),
        ).fetchall()
    else:
        total = db.execute("SELECT COUNT(*) as cnt FROM decisions").fetchone()["cnt"]
        rows = db.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset)
        ).fetchall()
    db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "decisions": [dict(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total": total,
    }


@app.get("/api/executions")
def api_executions(request: Request):
    """JSON endpoint for paginated executions."""
    db = _get_db()
    per_page = max(1, min(int(request.query_params.get("per_page", 10)), 100))
    page = max(1, int(request.query_params.get("page", 1)))
    symbol = request.query_params.get("symbol", "").strip().upper()
    offset = (page - 1) * per_page
    if symbol:
        total = db.execute(
            "SELECT COUNT(*) as cnt FROM executions WHERE UPPER(symbol) LIKE ?",
            (f"%{symbol}%",),
        ).fetchone()["cnt"]
        rows = db.execute(
            "SELECT e.*, d.action, d.confidence, d.reasoning "
            "FROM executions e "
            "LEFT JOIN decisions d ON e.decision_id = d.id "
            "WHERE UPPER(e.symbol) LIKE ? "
            "ORDER BY e.id DESC LIMIT ? OFFSET ?",
            (f"%{symbol}%", per_page, offset),
        ).fetchall()
    else:
        total = db.execute("SELECT COUNT(*) as cnt FROM executions").fetchone()["cnt"]
        rows = db.execute(
            "SELECT e.*, d.action, d.confidence, d.reasoning "
            "FROM executions e "
            "LEFT JOIN decisions d ON e.decision_id = d.id "
            "ORDER BY e.id DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
    db.close()
    executions = _enrich_executions([dict(r) for r in rows])
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "executions": executions,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total": total,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    db = _get_db()

    snapshot = db.execute("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()

    # Fetch live P&L breakdown (equity vs crypto)
    live_pl = _get_live_pl()

    # Prefer live positions from Alpaca, fall back to DB snapshot
    positions = _get_live_positions()
    if positions is None and snapshot and snapshot["positions_json"]:
        positions = json.loads(snapshot["positions_json"])
    positions = positions or []
    equity_positions = [p for p in positions if p.get("asset_class", "us_equity") != "crypto"]
    crypto_positions = [p for p in positions if p.get("asset_class") == "crypto"]

    # Paginated decisions (always start at page 1; AJAX handles navigation)
    per_page = 10
    page = 1
    offset = 0

    total_decisions = db.execute("SELECT COUNT(*) as cnt FROM decisions").fetchone()["cnt"]
    decisions = db.execute(
        "SELECT * FROM decisions ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset)
    ).fetchall()
    total_pages = max(1, (total_decisions + per_page - 1) // per_page)

    # Paginated executions (always start at page 1; AJAX handles navigation)
    exec_per_page = 10
    exec_page = 1
    exec_offset = 0

    total_executions = db.execute(
        "SELECT COUNT(*) as cnt FROM executions"
    ).fetchone()["cnt"]
    executions = db.execute(
        "SELECT e.*, d.action, d.confidence, d.reasoning "
        "FROM executions e "
        "LEFT JOIN decisions d ON e.decision_id = d.id "
        "ORDER BY e.id DESC LIMIT ? OFFSET ?",
        (exec_per_page, exec_offset),
    ).fetchall()
    executions = _enrich_executions([dict(e) for e in executions])

    exec_total_pages = max(1, (total_executions + exec_per_page - 1) // exec_per_page)

    equity_history = db.execute(
        "SELECT timestamp, equity, daily_pl, positions_json FROM portfolio_snapshots ORDER BY id DESC LIMIT 100"
    ).fetchall()
    equity_history = list(reversed(equity_history))

    # Compute equity vs crypto market value breakdown per snapshot
    for i, row in enumerate(equity_history):
        row = dict(row)
        equity_mv = 0.0
        crypto_mv = 0.0
        if row.get("positions_json"):
            try:
                positions_data = json.loads(row["positions_json"])
                for p in positions_data:
                    mv = float(p.get("market_value", 0))
                    if p.get("asset_class") == "crypto":
                        crypto_mv += mv
                    else:
                        equity_mv += mv
            except (json.JSONDecodeError, TypeError):
                pass
        # If no positions data, attribute all to equity
        if equity_mv == 0 and crypto_mv == 0:
            equity_mv = float(row.get("equity", 0))
        row["equity_mv"] = equity_mv
        row["crypto_mv"] = crypto_mv
        equity_history[i] = row

    db.close()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "version": APP_VERSION,
            "snapshot": snapshot,
            "live_pl": live_pl,
            "positions": positions,
            "equity_positions": equity_positions,
            "crypto_positions": crypto_positions,
            "decisions": decisions,
            "executions": executions,
            "equity_history": equity_history,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_decisions": total_decisions,
            "exec_page": exec_page,
            "exec_per_page": exec_per_page,
            "exec_total_pages": exec_total_pages,
            "total_executions": total_executions,
            "hold_all": _hold_all,
            "pause_ai": _pause_ai,
        },
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    decision_count = _get_decision_count()
    try:
        while True:
            # Push live P&L
            pl = _get_live_pl()
            if pl:
                await ws.send_text(json.dumps({"type": "pl", **pl}))

            # Push live positions
            positions = _get_live_positions()
            if positions is not None:
                await ws.send_text(json.dumps({"type": "positions", "positions": positions}))

            # Push countdown (initial delay or next scheduled cycle)
            if _first_cycle_at is not None:
                remaining = max(0, int(_first_cycle_at - time.time()))
            else:
                try:
                    db = _get_db()
                    saved = db.execute(
                        "SELECT value FROM scheduler_state WHERE key='next_cycle_at'"
                    ).fetchone()
                    db.close()
                    remaining = max(0, int(float(saved["value"]) - time.time())) if saved else 0
                except Exception:
                    remaining = 0
            await ws.send_text(json.dumps({"type": "countdown", "seconds": remaining}))

            # Push new-decisions notification
            new_count = _get_decision_count()
            if new_count > decision_count:
                await ws.send_text(json.dumps({"type": "new_decisions", "count": new_count}))
                decision_count = new_count

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


@app.get("/api/chart/{symbol:path}")
def api_chart(symbol: str, days: int = 30):
    """Return daily OHLC bars for a symbol."""
    if _alpaca_client is None:
        return {"error": "no alpaca client"}
    try:
        normalized = _alpaca_client.normalize_symbol(symbol)
        bars = _alpaca_client.get_bars([normalized], days=days)
        data = bars.get(normalized, [])
        return {"symbol": normalized, "bars": [b.__dict__ for b in data]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/force-cycle")
def api_force_cycle():
    """Trigger an immediate trading cycle."""
    if _run_cycle_fn is None:
        return {"error": "no cycle function registered"}

    threading.Thread(target=_run_cycle_fn, daemon=True).start()
    return {"status": "started"}


def is_hold_all() -> bool:
    """Check if HOLD ALL mode is active."""
    return _hold_all


def is_pause_ai() -> bool:
    """Check if Pause AI mode is active (rule-based only, no LLM calls)."""
    return _pause_ai


@app.post("/api/hold-all")
def api_hold_all():
    """Toggle HOLD ALL mode — pauses AI analysis while keeping position updates."""
    global _hold_all
    _hold_all = not _hold_all
    return {"hold_all": _hold_all}


@app.post("/api/pause-ai")
def api_pause_ai():
    """Toggle Pause AI mode — use rule-based analysis only, no LLM calls."""
    global _pause_ai
    _pause_ai = not _pause_ai
    return {"pause_ai": _pause_ai}


@app.post("/api/sell-all")
def api_sell_all():
    """Close all open positions immediately."""
    if _alpaca_client is None:
        return {"error": "no client configured"}
    try:
        results = _alpaca_client.close_all_positions()
        return {"status": "ok", "closed": results}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/positions")
def api_positions():
    """Return current live positions."""
    positions = _get_live_positions()
    return {"positions": positions or []}
