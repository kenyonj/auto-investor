"""Execution engine — ties together analysis, risk, and order execution."""

from rich.console import Console
from rich.table import Table

from auto_investor.agents import AnalystAgent
from auto_investor.clients import AlpacaClient
from auto_investor.config import AppConfig, Secrets, load_config
from auto_investor.data import DataStore
from auto_investor.models import Action
from auto_investor.risk import RiskManager

console = Console()


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
        self.risk = RiskManager(self.config.risk)
        self.store = DataStore()

    def run_cycle(self, dry_run: bool = True):
        """Run one full analysis → decision → execution cycle."""
        console.rule("[bold blue]Auto-Investor Cycle")

        # 1. Get portfolio state
        console.print("[dim]Fetching portfolio...[/dim]")
        portfolio = self.alpaca.get_portfolio_snapshot()
        self.store.log_snapshot(portfolio)

        console.print(f"  Equity: ${portfolio.equity:,.2f}")
        console.print(f"  Cash: ${portfolio.cash:,.2f}")
        console.print(f"  Daily P&L: ${portfolio.daily_pl:+,.2f} ({portfolio.daily_pl_pct:+.2f}%)")

        # 2. Get market data
        console.print("[dim]Fetching quotes...[/dim]")
        quotes = self.alpaca.get_quotes(self.config.watchlist)

        # 3. AI analysis
        console.print("[dim]Running AI analysis...[/dim]")
        decisions = self.agent.analyze(portfolio, quotes, self.config.watchlist)

        # 4. Risk checks
        console.print("[dim]Applying risk checks...[/dim]")
        approved = self.risk.evaluate(decisions, portfolio)

        # 5. Display decisions
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

        # 6. Execute (if not dry run)
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
                        order = self.alpaca.execute_decision(d)
                        if order:
                            self.store.log_execution(decision_id, order)
                            console.print(
                                f"  [green]✓[/green] {d.action.value.upper()} "
                                f"{d.quantity} {d.symbol} — {order['status']}"
                            )
                    except Exception as e:
                        console.print(f"  [red]✗[/red] {d.symbol}: {e}")

        console.rule("[bold blue]Cycle Complete")
