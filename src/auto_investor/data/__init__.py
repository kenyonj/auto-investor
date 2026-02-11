"""SQLite-backed decision and trade logger."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from auto_investor.models import TradeDecision


class DataStore:
    """Logs trade decisions and executions for auditability."""

    def __init__(self, db_path: str | Path = "auto_investor.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence TEXT NOT NULL,
                quantity REAL,
                reasoning TEXT,
                risk_notes TEXT,
                vetoed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                decision_id INTEGER,
                order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT,
                order_type TEXT,
                status TEXT,
                filled_avg_price REAL,
                filled_qty REAL,
                FOREIGN KEY (decision_id) REFERENCES decisions(id)
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity REAL,
                cash REAL,
                buying_power REAL,
                daily_pl REAL,
                positions_json TEXT
            );

            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS loss_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                sold_at_loss REAL NOT NULL
            );
        """)
        self.conn.commit()

        # Migrate existing DBs: add new columns if missing
        existing_cols = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(executions)").fetchall()
        }
        for col, col_type in [("filled_avg_price", "REAL"), ("filled_qty", "REAL")]:
            if col not in existing_cols:
                self.conn.execute(f"ALTER TABLE executions ADD COLUMN {col} {col_type}")
        self.conn.commit()

    def reset(self):
        """Drop all data tables and recreate them."""
        self.conn.executescript("""
            DELETE FROM decisions;
            DELETE FROM executions;
            DELETE FROM portfolio_snapshots;
            DELETE FROM loss_sales;
            DELETE FROM scheduler_state;
        """)
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        """Get a scheduler state value."""
        row = self.conn.execute(
            "SELECT value FROM scheduler_state WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        """Set a scheduler state value."""
        self.conn.execute(
            "INSERT OR REPLACE INTO scheduler_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def log_decision(self, decision: TradeDecision, vetoed: bool = False) -> int:
        """Log a trade decision. Returns the row ID."""
        cursor = self.conn.execute(
            """INSERT INTO decisions
               (timestamp, symbol, action, confidence, quantity, reasoning, risk_notes, vetoed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.timestamp.isoformat(),
                decision.symbol,
                decision.action.value,
                decision.confidence.value,
                decision.quantity,
                decision.reasoning,
                decision.risk_notes,
                1 if vetoed else 0,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def log_execution(self, decision_id: int, order: dict):
        """Log an executed order."""
        self.conn.execute(
            """INSERT INTO executions
               (timestamp, decision_id, order_id, symbol, side, quantity,
                order_type, status, filled_avg_price, filled_qty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                decision_id,
                order.get("id"),
                order.get("symbol"),
                order.get("side"),
                order.get("qty"),
                order.get("type"),
                order.get("status"),
                order.get("filled_avg_price"),
                order.get("filled_qty"),
            ),
        )
        self.conn.commit()

    def log_snapshot(self, snapshot):
        """Log a portfolio snapshot."""
        positions_json = json.dumps([p.model_dump() for p in snapshot.positions], default=str)
        self.conn.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, equity, cash, buying_power, daily_pl, positions_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot.timestamp.isoformat(),
                snapshot.equity,
                snapshot.cash,
                snapshot.buying_power,
                snapshot.daily_pl,
                positions_json,
            ),
        )
        self.conn.commit()

    def log_loss_sale(self, symbol: str, loss_amount: float) -> None:
        """Record a sale that realized a loss (for wash sale tracking)."""
        self.conn.execute(
            "INSERT INTO loss_sales (timestamp, symbol, sold_at_loss) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), symbol, loss_amount),
        )
        self.conn.commit()

    def get_recent_loss_sale(self, symbol: str, days: int = 30) -> dict | None:
        """Check if a symbol was sold at a loss within the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        row = self.conn.execute(
            "SELECT timestamp, sold_at_loss FROM loss_sales "
            "WHERE symbol = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
        if row:
            return {"timestamp": row[0], "loss": row[1]}
        return None

    def get_last_buy_time(self, symbol: str) -> datetime | None:
        """Get the timestamp of the most recent executed BUY for a symbol."""
        row = self.conn.execute(
            "SELECT timestamp FROM executions WHERE symbol = ? AND side = 'buy' "
            "ORDER BY timestamp DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row:
            return datetime.fromisoformat(row[0])
        return None

    def close(self):
        self.conn.close()
