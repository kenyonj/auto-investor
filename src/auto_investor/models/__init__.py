"""Pydantic models for trade decisions, positions, and market data."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TradeDecision(BaseModel):
    """A structured trade decision from the AI agent."""

    symbol: str
    action: Action
    confidence: Confidence
    quantity: float | None = None
    limit_price: float | None = None
    reasoning: str
    risk_notes: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class Position(BaseModel):
    """Current portfolio position."""

    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    asset_class: str = "us_equity"


class PortfolioSnapshot(BaseModel):
    """Point-in-time portfolio state."""

    timestamp: datetime = Field(default_factory=datetime.now)
    equity: float
    cash: float
    buying_power: float
    positions: list[Position]
    daily_pl: float = 0.0
    daily_pl_pct: float = 0.0


class MarketQuote(BaseModel):
    """Basic market quote for a ticker."""

    symbol: str
    price: float
    change: float
    change_pct: float
    volume: int
    timestamp: datetime


class DailyBar(BaseModel):
    """Single day OHLCV bar."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
