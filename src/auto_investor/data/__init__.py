"""SQLite-backed decision and trade logger."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from auto_investor.models import TradeDecision


class DataStore:
    """Logs trade decisions and executions for auditability."""

    def __init__(self, db_path: str | Path = "auto_investor.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence TEXT NOT NULL,
                quantity INTEGER,
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
        """)
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
               (timestamp, decision_id, order_id, symbol, side, quantity, order_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                decision_id,
                order.get("id"),
                order.get("symbol"),
                order.get("side"),
                order.get("qty"),
                order.get("type"),
                order.get("status"),
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

    def close(self):
        self.conn.close()
