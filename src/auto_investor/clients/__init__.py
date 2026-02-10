"""Alpaca API client wrapper for trading and market data."""

from datetime import datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestQuoteRequest,
    MarketMoversRequest,
    MostActivesRequest,
    NewsRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from auto_investor.config import Secrets
from auto_investor.models import (
    Action,
    DailyBar,
    MarketQuote,
    NewsArticle,
    PortfolioSnapshot,
    Position,
    TradeDecision,
)


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
        self.screener = ScreenerClient(
            api_key=self.secrets.alpaca_api_key,
            secret_key=self.secrets.alpaca_secret_key,
        )
        self.crypto_data = CryptoHistoricalDataClient(
            api_key=self.secrets.alpaca_api_key,
            secret_key=self.secrets.alpaca_secret_key,
        )
        self.news_client = NewsClient(
            api_key=self.secrets.alpaca_api_key,
            secret_key=self.secrets.alpaca_secret_key,
        )

    # Known crypto base tickers (without /USD suffix)
    _CRYPTO_BASES = {s.split("/")[0] for s in [
        "AAVE/USD", "AVAX/USD", "BAT/USD", "BCH/USD", "BTC/USD",
        "CRV/USD", "DOGE/USD", "DOT/USD", "ETH/USD", "GRT/USD",
        "LINK/USD", "LTC/USD", "PEPE/USD", "SHIB/USD", "SKY/USD",
        "SOL/USD", "SUSHI/USD", "TRUMP/USD", "UNI/USD", "XRP/USD",
        "XTZ/USD", "YFI/USD",
    ]}

    @classmethod
    def is_crypto(cls, symbol: str) -> bool:
        """Check if a symbol is a crypto pair (BTC/USD or BTCUSD)."""
        if "/" in symbol:
            return True
        upper = symbol.upper()
        if upper.endswith("USD"):
            base = upper[:-3]
            return base in cls._CRYPTO_BASES
        return False

    @classmethod
    def normalize_symbol(cls, symbol: str) -> str:
        """Normalize symbol: convert BTCUSD → BTC/USD for crypto."""
        upper = symbol.upper()
        if "/" not in upper and upper.endswith("USD"):
            base = upper[:-3]
            if base in cls._CRYPTO_BASES:
                return f"{base}/USD"
        return upper

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
                symbol=self.normalize_symbol(p.symbol),
                quantity=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_pl_pct=float(p.unrealized_plpc) * 100,
                asset_class=p.asset_class.value if p.asset_class else "us_equity",
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
        """Get latest quotes for a list of symbols (stocks and/or crypto)."""
        stock_syms = [s for s in symbols if not self.is_crypto(s)]
        crypto_syms = [s for s in symbols if self.is_crypto(s)]
        results = []

        if stock_syms:
            request = StockLatestQuoteRequest(symbol_or_symbols=stock_syms)
            quotes = self.data.get_stock_latest_quote(request)
            for symbol, quote in quotes.items():
                # Use mid-price for more accurate P&L (avoid ask-side bias)
                bid = float(quote.bid_price)
                ask = float(quote.ask_price)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ask
                results.append(
                    MarketQuote(
                        symbol=symbol,
                        price=mid,
                        change=0,
                        change_pct=0,
                        volume=0,
                        timestamp=datetime.now(),
                    )
                )

        if crypto_syms:
            request = CryptoLatestQuoteRequest(symbol_or_symbols=crypto_syms)
            quotes = self.crypto_data.get_crypto_latest_quote(request)
            for symbol, quote in quotes.items():
                bid = float(quote.bid_price)
                ask = float(quote.ask_price)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ask
                results.append(
                    MarketQuote(
                        symbol=symbol,
                        price=mid,
                        change=0,
                        change_pct=0,
                        volume=0,
                        timestamp=datetime.now(),
                    )
                )

        return results

    def get_top_movers(self, top: int = 10) -> list[str]:
        """Get today's top movers (gainers + losers) by % change."""
        movers = self.screener.get_market_movers(MarketMoversRequest(top=top))
        symbols = []
        for m in movers.gainers:
            symbols.append(m.symbol)
        for m in movers.losers:
            symbols.append(m.symbol)
        return symbols

    def get_most_active(self, top: int = 10) -> list[str]:
        """Get today's most actively traded stocks by volume."""
        actives = self.screener.get_most_actives(MostActivesRequest(top=top))
        return [a.symbol for a in actives.most_actives]

    # All Alpaca-supported crypto coins (traded as /USD pairs)
    CRYPTO_UNIVERSE = [
        "AAVE/USD", "AVAX/USD", "BAT/USD", "BCH/USD", "BTC/USD",
        "CRV/USD", "DOGE/USD", "DOT/USD", "ETH/USD", "GRT/USD",
        "LINK/USD", "LTC/USD", "PEPE/USD", "SHIB/USD", "SKY/USD",
        "SOL/USD", "SUSHI/USD", "TRUMP/USD", "UNI/USD", "XRP/USD",
        "XTZ/USD", "YFI/USD",
    ]

    def get_crypto_movers(self, top: int = 10) -> list[str]:
        """Get top crypto movers by 24h % change from the full Alpaca universe."""
        try:
            bars = self.get_bars(self.CRYPTO_UNIVERSE, days=2)
            changes = []
            for symbol, symbol_bars in bars.items():
                if len(symbol_bars) >= 2:
                    prev_close = symbol_bars[-2].close
                    curr_close = symbol_bars[-1].close
                    if prev_close > 0:
                        pct = abs((curr_close - prev_close) / prev_close) * 100
                        changes.append((symbol, pct))
            changes.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in changes[:top]]
        except Exception:
            return []

    def get_order_status(self, order_id: str) -> str | None:
        """Get the current status of an order by its ID."""
        try:
            order = self.trading.get_order_by_id(order_id)
            return str(order.status.value) if order.status else None
        except Exception:
            return None

    def get_order_details(self, order_id: str) -> dict | None:
        """Get status and fill details for an order."""
        try:
            order = self.trading.get_order_by_id(order_id)
            return {
                "status": str(order.status.value) if order.status else None,
                "filled_avg_price": (
                    float(order.filled_avg_price) if order.filled_avg_price else None
                ),
                "filled_qty": float(order.filled_qty) if order.filled_qty else None,
            }
        except Exception:
            return None

    def get_bars(self, symbols: list[str], days: int = 5) -> dict[str, list[DailyBar]]:
        """Get daily bars for the last N trading days (stocks and/or crypto)."""
        stock_syms = [s for s in symbols if not self.is_crypto(s)]
        crypto_syms = [s for s in symbols if self.is_crypto(s)]
        result: dict[str, list[DailyBar]] = {}
        start = datetime.now() - timedelta(days=days + 5)

        if stock_syms:
            request = StockBarsRequest(
                symbol_or_symbols=stock_syms,
                timeframe=TimeFrame.Day,
                start=start,
                limit=days,
            )
            bars = self.data.get_stock_bars(request)
            for symbol in stock_syms:
                symbol_bars = bars.data.get(symbol, [])
                result[symbol] = [
                    DailyBar(
                        date=b.timestamp.strftime("%Y-%m-%d"),
                        open=float(b.open),
                        high=float(b.high),
                        low=float(b.low),
                        close=float(b.close),
                        volume=int(b.volume),
                    )
                    for b in symbol_bars
                ]

        if crypto_syms:
            request = CryptoBarsRequest(
                symbol_or_symbols=crypto_syms,
                timeframe=TimeFrame.Day,
                start=start,
                limit=days,
            )
            bars = self.crypto_data.get_crypto_bars(request)
            for symbol in crypto_syms:
                symbol_bars = bars.data.get(symbol, [])
                result[symbol] = [
                    DailyBar(
                        date=b.timestamp.strftime("%Y-%m-%d"),
                        open=float(b.open),
                        high=float(b.high),
                        low=float(b.low),
                        close=float(b.close),
                        volume=int(b.volume),
                    )
                    for b in symbol_bars
                ]

        return result

    def get_news(self, symbols: list[str], limit: int = 5) -> dict[str, list[NewsArticle]]:
        """Get recent news articles for a list of symbols."""
        # Alpaca expects comma-separated symbols without /USD for crypto
        clean_syms = [s.replace("/", "") for s in symbols]
        try:
            request = NewsRequest(
                symbols=",".join(clean_syms),
                limit=limit * len(clean_syms),
                include_content=False,
                exclude_contentless=True,
                sort="DESC",
            )
            news_set = self.news_client.get_news(request)
            result: dict[str, list[NewsArticle]] = {s: [] for s in symbols}
            for article in news_set.data.get("news", []):
                for sym in symbols:
                    clean = sym.replace("/", "")
                    if clean in article.symbols and len(result[sym]) < limit:
                        result[sym].append(
                            NewsArticle(
                                headline=article.headline,
                                summary=article.summary or "",
                                source=article.source,
                                created_at=article.created_at,
                                symbols=article.symbols,
                            )
                        )
            return result
        except Exception:
            return {}

    @staticmethod
    def _is_regular_hours() -> bool:
        """Check if current time is within regular market hours (9:30–16:00 ET)."""
        now = datetime.now()
        regular_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        regular_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return regular_open <= now <= regular_close and now.weekday() < 5

    def get_open_orders(self, symbol: str) -> list:
        """Get open/pending orders for a symbol."""
        try:
            from alpaca.trading.requests import GetOrdersRequest

            req = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[symbol],
            )
            return self.trading.get_orders(req)
        except Exception:
            return []

    def execute_decision(
        self, decision: TradeDecision, current_price: float | None = None
    ) -> dict | None:
        """Execute a trade decision. Returns order details or None if hold.

        During extended hours, market orders are not supported so we automatically
        convert to limit orders using the current quote price.
        """
        if decision.action == Action.HOLD:
            return None

        # Skip if there are already open orders for this symbol
        open_orders = self.get_open_orders(decision.symbol)
        if open_orders:
            sides = {str(o.side.value) for o in open_orders}
            raise ValueError(
                f"open {'/'.join(sides)} order(s) already exist for "
                f"{decision.symbol} — skipping"
            )

        side = OrderSide.BUY if decision.action == Action.BUY else OrderSide.SELL
        is_crypto = self.is_crypto(decision.symbol)

        if is_crypto:
            # Crypto: always market orders with GTC
            request = MarketOrderRequest(
                symbol=decision.symbol,
                qty=decision.quantity,
                side=side,
                time_in_force=TimeInForce.GTC,
            )
        elif decision.limit_price:
            extended = not self._is_regular_hours()
            request = LimitOrderRequest(
                symbol=decision.symbol,
                qty=decision.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=decision.limit_price,
                extended_hours=extended,
            )
        elif not self._is_regular_hours() and current_price:
            # Extended hours requires limit orders; use current price with small buffer
            buffer = 1.005 if side == OrderSide.BUY else 0.995
            request = LimitOrderRequest(
                symbol=decision.symbol,
                qty=decision.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(current_price * buffer, 2),
                extended_hours=True,
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
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_qty": float(order.filled_qty) if order.filled_qty else None,
        }
