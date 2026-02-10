"""CLI entrypoint for auto-investor."""

import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta

import uvicorn
from apscheduler.schedulers.blocking import BlockingScheduler
from rich.console import Console

from auto_investor.config import Secrets, load_config
from auto_investor.execution import ExecutionEngine

console = Console()

DASHBOARD_PORT = int(os.environ.get("PORT", 8000))


def _start_dashboard():
    """Run the dashboard in a background thread."""
    from auto_investor.dashboard import app

    config = uvicorn.Config(app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    console.print(f"[bold green]Dashboard[/bold green] → http://localhost:{DASHBOARD_PORT}")


def main():
    config = load_config()

    # --reset: wipe the database and start fresh
    if "--reset" in sys.argv:
        db_path = os.environ.get("DB_PATH", "auto_investor.db")
        if os.path.exists(db_path):
            os.remove(db_path)
            console.print(f"[green]Deleted {db_path} — starting fresh[/green]")
        else:
            console.print("[dim]No database to reset[/dim]")
        if len(sys.argv) <= 2:
            return

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
    if config.crypto_watchlist:
        console.print(f"Crypto: {', '.join(config.crypto_watchlist)}")

    engine = ExecutionEngine(config, secrets)

    dry_run = "--execute" not in sys.argv
    if dry_run:
        console.print("[yellow]Running in DRY RUN mode (pass --execute to submit orders)[/yellow]")

    # Start dashboard if --schedule or --dashboard
    if "--schedule" in sys.argv or "--dashboard" in sys.argv:
        from auto_investor.dashboard import set_alpaca_client, set_run_cycle_fn

        set_alpaca_client(engine.alpaca)

    # --schedule: run continuously on a configurable interval during market hours
    if "--schedule" in sys.argv:
        schedule = config.trading.schedule
        open_h, open_m = (int(x) for x in schedule.market_open.split(":"))
        close_h, close_m = (int(x) for x in schedule.market_close.split(":"))

        def _next_market_open_ts() -> float:
            """Return timestamp of the next market open (skipping weekends)."""
            now = datetime.now()
            nxt = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            # Skip weekends
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return nxt.timestamp()

        def guarded_cycle():
            from auto_investor.dashboard import is_hold_all, is_pause_ai

            if is_hold_all():
                console.print("[yellow]⏸ HOLD ALL active — skipping AI analysis[/yellow]")
                # Still log portfolio snapshot for tracking
                try:
                    portfolio = engine.alpaca.get_portfolio_snapshot()
                    engine.store.log_snapshot(portfolio)
                except Exception:
                    pass
                return

            skip_ai = is_pause_ai()
            if skip_ai:
                console.print("[yellow]🤖 AI paused — using rule-based analysis only[/yellow]")

            now = datetime.now()
            is_weekday = now.weekday() < 5
            market_open = now.replace(hour=open_h, minute=open_m, second=0)
            market_close = now.replace(hour=close_h, minute=close_m, second=0)
            equity_hours = is_weekday and market_open <= now <= market_close

            if equity_hours:
                try:
                    engine.run_cycle(dry_run=dry_run, skip_ai=skip_ai)
                except Exception as e:
                    console.print(f"[red]Equity cycle error: {e}[/red]")

            if config.crypto_watchlist:
                try:
                    engine.run_cycle(dry_run=dry_run, crypto=True, skip_ai=skip_ai)
                except Exception as e:
                    console.print(f"[red]Crypto cycle error: {e}[/red]")

            if not equity_hours and not config.crypto_watchlist:
                console.print(
                    f"[dim]Outside market hours"
                    f" ({schedule.market_open}–{schedule.market_close}), skipping[/dim]"
                )

            # Set next cycle time
            if equity_hours or config.crypto_watchlist:
                next_at = time.time() + schedule.interval_minutes * 60
            else:
                next_at = _next_market_open_ts()
            engine.store.set_state("next_cycle_at", str(next_at))

        # Compute initial delay: resume from persisted schedule or use 15s for fresh start
        saved_next = engine.store.get_state("next_cycle_at")
        now_ts = time.time()
        if saved_next:
            remaining = float(saved_next) - now_ts
            startup_delay = max(5, int(remaining))  # at least 5s
        else:
            startup_delay = 15

        from auto_investor.dashboard import set_first_cycle_time

        first_cycle_at = now_ts + startup_delay
        set_first_cycle_time(first_cycle_at)

        def delayed_first_cycle():
            time.sleep(startup_delay)
            set_first_cycle_time(None)
            guarded_cycle()

        threading.Thread(target=delayed_first_cycle, daemon=True).start()
        console.print(f"[dim]First cycle in {startup_delay}s...[/dim]")

        # Register guarded_cycle for dashboard "Run Analysis" button
        set_run_cycle_fn(guarded_cycle)
        _start_dashboard()

        scheduler = BlockingScheduler()
        scheduler.add_job(
            guarded_cycle,
            "interval",
            minutes=schedule.interval_minutes,
            id="trading_cycle",
            name="trading_cycle",
        )

        sched_msg = (
            f"\n[bold green]Scheduler active[/bold green] — "
            f"every {schedule.interval_minutes}min, "
            f"equities {schedule.market_open}–{schedule.market_close} Mon–Fri"
        )
        if config.crypto_watchlist:
            sched_msg += ", crypto 24/7"
        console.print(sched_msg)
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown())

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            console.print("\n[bold]Scheduler stopped.[/bold]")
    elif "--dashboard" in sys.argv:
        set_run_cycle_fn(
            lambda: engine.run_cycle(dry_run=dry_run)
        )
        _start_dashboard()
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        try:
            signal.pause()
        except (KeyboardInterrupt, SystemExit):
            console.print("\n[bold]Dashboard stopped.[/bold]")
    else:
        engine.run_cycle(dry_run=dry_run)


if __name__ == "__main__":
    main()
