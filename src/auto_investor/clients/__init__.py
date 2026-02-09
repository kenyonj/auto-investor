"""Alpaca API client wrapper for trading and market data."""

from datetime import datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest

from auto_investor.config import Secrets
from auto_investor.models import Action, MarketQuote, Position, PortfolioSnapshot, TradeDecision


class AlpacaClient:
    """Wrapper around Alpaca's trading and data APIs."""

    def __init__(self, secrets: Secrets | None = None):
        self.secrets = secrets or Secrets()
        self.trading = TradingClient(
            api_key=self.secrets.alpaca_api_key,
            secret_key=self.secrets.alpaca_secret_key,
            paper=True,  # Always paper until explicitly changed
        )
        self.data = StockHistoricalDataClient(
            api_key=self.secrets.alpaca_api_key,
            secret_key=self.secrets.alpaca_secret_key,
        )

    def get_account(self) -> dict:
        """Get account info."""
        account = self.trading.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "daily_pl": float(account.equity) - float(account.last_equity),
        }

    def get_positions(self) -> list[Position]:
        """Get all current positions."""
        raw = self.trading.get_all_positions()
        return [
            Position(
                symbol=p.symbol,
                quantity=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_pl_pct=float(p.unrealized_plpc) * 100,
            )
            for p in raw
        ]

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        """Get a full portfolio snapshot."""
        account = self.get_account()
        positions = self.get_positions()
        return PortfolioSnapshot(
            equity=account["equity"],
            cash=account["cash"],
            buying_power=account["buying_power"],
            positions=positions,
            daily_pl=account["daily_pl"],
            daily_pl_pct=(account["daily_pl"] / account["equity"]) * 100
            if account["equity"]
            else 0,
        )

    def get_quotes(self, symbols: list[str]) -> list[MarketQuote]:
        """Get latest quotes for a list of symbols."""
        request = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = self.data.get_stock_latest_quote(request)
        results = []
        for symbol, quote in quotes.items():
            results.append(
                MarketQuote(
                    symbol=symbol,
                    price=float(quote.ask_price),
                    change=0,  # Latest quote doesn't include change
                    change_pct=0,
                    volume=0,
                    timestamp=datetime.now(),
                )
            )
        return results

    def execute_decision(self, decision: TradeDecision) -> dict | None:
        """Execute a trade decision. Returns order details or None if hold."""
        if decision.action == Action.HOLD:
            return None

        side = OrderSide.BUY if decision.action == Action.BUY else OrderSide.SELL

        if decision.limit_price:
            request = LimitOrderRequest(
                symbol=decision.symbol,
                qty=decision.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=decision.limit_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=decision.symbol,
                qty=decision.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
            )

        order = self.trading.submit_order(request)
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": str(order.qty),
            "type": order.type.value,
            "status": order.status.value,
        }
