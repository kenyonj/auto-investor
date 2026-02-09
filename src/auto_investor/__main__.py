"""CLI entrypoint for auto-investor."""

import sys

from rich.console import Console

from auto_investor.config import Secrets, load_config
from auto_investor.execution import ExecutionEngine

console = Console()


def main():
    config = load_config()
    secrets = Secrets()

    # Validate credentials
    if not secrets.alpaca_api_key or not secrets.alpaca_secret_key:
        console.print("[red]Error: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set[/red]")
        sys.exit(1)
    if not secrets.anthropic_api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY must be set[/red]")
        sys.exit(1)

    console.print(f"[bold]auto-investor v0.1.0[/bold] — mode: {config.trading.mode}")
    console.print(f"Watchlist: {', '.join(config.watchlist)}")

    engine = ExecutionEngine(config, secrets)

    # For now, just run a single dry-run cycle
    dry_run = "--execute" not in sys.argv
    if dry_run:
        console.print("[yellow]Running in DRY RUN mode (pass --execute to submit orders)[/yellow]")

    engine.run_cycle(dry_run=dry_run)


if __name__ == "__main__":
    main()
