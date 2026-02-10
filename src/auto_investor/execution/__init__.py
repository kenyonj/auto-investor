"""Execution engine — ties together analysis, risk, and order execution."""

import os
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from auto_investor.agents import AnalystAgent
from auto_investor.clients import AlpacaClient
from auto_investor.clients.reddit import RedditClient
from auto_investor.config import AppConfig, Secrets, load_config
from auto_investor.data import DataStore
from auto_investor.models import Action
from auto_investor.risk import RiskManager

console = Console()

# Symbols recently decided as HOLD are skipped for this many minutes
HOLD_COOLDOWN_MINUTES = 20


class ExecutionEngine:
    """Orchestrates the full analysis → risk check → execute pipeline."""

    def __init__(
        self,
        config: AppConfig | None = None,
        secrets: Secrets | None = None,
    ):
        self.config = config or load_config()
        self.secrets = secrets or Secrets()
        self.alpaca = AlpacaClient(self.secrets)
        self.agent = AnalystAgent(self.config.ai, self.secrets)
        self.store = DataStore(os.environ.get("DB_PATH", "auto_investor.db"))
        self.risk = RiskManager(self.config.risk, store=self.store)
        self.reddit = RedditClient(self.config.reddit_subreddits)
        self._hold_cooldowns: dict[str, datetime] = {}

    def _apply_cooldowns(self, watchlist: list[str]) -> list[str]:
        """Filter out symbols on HOLD cooldown."""
        now = datetime.now()
        cutoff = now - timedelta(minutes=HOLD_COOLDOWN_MINUTES)
        active = []
        skipped = []
        for symbol in watchlist:
            last_hold = self._hold_cooldowns.get(symbol)
            if last_hold and last_hold > cutoff:
                skipped.append(symbol)
            else:
                active.append(symbol)
        if skipped:
            console.print(
                f"  [dim]Cooldown (HOLD <{HOLD_COOLDOWN_MINUTES}m ago): {', '.join(skipped)}[/dim]"
            )
        return active

    def _record_holds(self, decisions: list) -> None:
        """Record HOLD decisions for cooldown tracking."""
        now = datetime.now()
        for d in decisions:
            if d.action == Action.HOLD:
                self._hold_cooldowns[d.symbol] = now

    def run_cycle(self, dry_run: bool = True, crypto: bool = False):
        """Run one full analysis → decision → execution cycle."""
        label = "Crypto" if crypto else "Equity"
        console.rule(f"[bold blue]Auto-Investor {label} Cycle")

        if crypto:
            watchlist = list(self.config.crypto_watchlist)
            # Add dynamic crypto movers
            console.print("[dim]Fetching crypto movers...[/dim]")
            try:
                crypto_movers = self.alpaca.get_crypto_movers(top=10)
            except Exception as e:
                console.print(f"  [dim]Could not fetch crypto movers: {e}[/dim]")
                crypto_movers = []
            watchlist = list(dict.fromkeys(watchlist + crypto_movers))
            if not watchlist:
                console.print("[yellow]No crypto watchlist configured, skipping[/yellow]")
                console.rule(f"[bold blue]{label} Cycle Complete")
                return
        else:
            # Build dynamic watchlist: core tickers + today's top movers
            console.print("[dim]Fetching top movers...[/dim]")
            try:
                movers = self.alpaca.get_top_movers(top=10)
            except Exception as e:
                console.print(f"  [dim]Could not fetch movers: {e}[/dim]")
                movers = []
            watchlist = list(dict.fromkeys(self.config.watchlist + movers))

        # Apply HOLD cooldowns (skip for crypto — 24/7 market)
        if not crypto:
            watchlist = self._apply_cooldowns(watchlist)
        if not watchlist:
            console.print("[yellow]All symbols on cooldown, skipping cycle[/yellow]")
            console.rule(f"[bold blue]{label} Cycle Complete")
            return

        console.print(f"  Watchlist: {', '.join(watchlist)}")

        # 2. Get portfolio state
        console.print("[dim]Fetching portfolio...[/dim]")
        portfolio = self.alpaca.get_portfolio_snapshot()
        self.store.log_snapshot(portfolio)

        console.print(f"  Equity: ${portfolio.equity:,.2f}")
        console.print(f"  Cash: ${portfolio.cash:,.2f}")
        console.print(f"  Daily P&L: ${portfolio.daily_pl:+,.2f} ({portfolio.daily_pl_pct:+.2f}%)")

        # 3. Get market data
        console.print("[dim]Fetching quotes...[/dim]")
        quotes = self.alpaca.get_quotes(watchlist)
        quote_prices = {q.symbol: q.price for q in quotes}

        # 4. Fetch recent price history
        console.print("[dim]Fetching 5-day price history...[/dim]")
        try:
            bars = self.alpaca.get_bars(watchlist, days=5)
        except Exception as e:
            console.print(f"  [dim]Could not fetch bars: {e}[/dim]")
            bars = {}

        # 4b. Fetch recent news
        console.print("[dim]Fetching recent news...[/dim]")
        try:
            news = self.alpaca.get_news(watchlist, limit=3)
        except Exception as e:
            console.print(f"  [dim]Could not fetch news: {e}[/dim]")
            news = {}

        # 4c. Fetch Reddit sentiment
        console.print("[dim]Fetching Reddit sentiment...[/dim]")
        try:
            reddit_posts = self.reddit.get_posts(limit=5)
        except Exception as e:
            console.print(f"  [dim]Could not fetch Reddit posts: {e}[/dim]")
            reddit_posts = []

        # 5. AI analysis
        console.print("[dim]Running AI analysis...[/dim]")
        decisions = self.agent.analyze(
            portfolio, quotes, watchlist, bars=bars, news=news, reddit_posts=reddit_posts
        )

        # 6. Risk checks
        console.print("[dim]Applying risk checks...[/dim]")
        approved = self.risk.evaluate(decisions, portfolio)

        # Record HOLD cooldowns
        self._record_holds(approved)

        # 7. Display decisions
        table = Table(title="Trade Decisions")
        table.add_column("Symbol", style="cyan")
        table.add_column("Action", style="bold")
        table.add_column("Confidence")
        table.add_column("Qty", justify="right")
        table.add_column("Reasoning")
        table.add_column("Risk Notes", style="dim")

        for d in approved:
            action_style = {
                Action.BUY: "green",
                Action.SELL: "red",
                Action.HOLD: "yellow",
            }.get(d.action, "white")

            table.add_row(
                d.symbol,
                f"[{action_style}]{d.action.value.upper()}[/{action_style}]",
                d.confidence.value,
                str(d.quantity or "-"),
                d.reasoning[:60] + "..." if len(d.reasoning) > 60 else d.reasoning,
                d.risk_notes[:40] if d.risk_notes else "",
            )

        console.print(table)

        # 8. Execute (if not dry run)
        if dry_run:
            console.print("[yellow]DRY RUN — no orders submitted[/yellow]")
            for d in approved:
                vetoed = "VETOED" in d.risk_notes
                self.store.log_decision(d, vetoed=vetoed)
        else:
            for d in approved:
                vetoed = "VETOED" in d.risk_notes
                decision_id = self.store.log_decision(d, vetoed=vetoed)

                if d.action != Action.HOLD and not vetoed:
                    try:
                        # Pass current price for extended-hours limit order conversion
                        price = quote_prices.get(d.symbol)
                        order = self.alpaca.execute_decision(d, current_price=price)
                        if order:
                            self.store.log_execution(decision_id, order)
                            console.print(
                                f"  [green]✓[/green] {d.action.value.upper()} "
                                f"{d.quantity} {d.symbol} — {order['status']}"
                            )
                            # Track loss sales for wash sale prevention (not for crypto)
                            if d.action == Action.SELL and "/" not in d.symbol:
                                pos = next(
                                    (p for p in portfolio.positions if p.symbol == d.symbol),
                                    None,
                                )
                                if pos and pos.unrealized_pl < 0:
                                    self.store.log_loss_sale(d.symbol, pos.unrealized_pl)
                                    console.print(
                                        f"  [yellow]⚠ Wash sale lock: {d.symbol} "
                                        f"(loss ${pos.unrealized_pl:,.2f}) — "
                                        f"no rebuy for 30 days[/yellow]"
                                    )
                    except Exception as e:
                        console.print(f"  [red]✗[/red] {d.symbol}: {e}")

        console.rule(f"[bold blue]{label} Cycle Complete")
