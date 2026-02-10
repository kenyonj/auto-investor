# auto-investor

AI-driven automated trading bot using Claude + Alpaca for equities and crypto.

## Overview

An AI agent that analyzes market conditions, researches stocks/ETFs/crypto, and executes trades automatically via Alpaca's API. Claude provides the analytical reasoning; risk management guardrails keep things safe.

- **Equities**: Core watchlist + dynamic top movers from Alpaca screener, traded during extended market hours (Mon–Fri)
- **Crypto**: Core watchlist + dynamic movers scanned from all 22 Alpaca-supported /USD pairs, traded 24/7
- **Dashboard**: Real-time web UI with WebSocket updates, equity/crypto P&L split, position tables, decision history, and execution tracking

**⚠️ Paper trading only until explicitly promoted to live.**

## Architecture

```
Orchestrator (scheduler)
├── Market Data Feed (Alpaca) — quotes, price history, top movers, crypto movers
├── AI Agent (Claude) — analyze, research, decide (with crypto-specific guidelines)
├── Risk Manager — position limits, drawdown protection, wash sale prevention (equities only)
├── Execution Engine — Alpaca orders (equities + crypto)
├── Data Store (SQLite) — trades, decisions, snapshots, loss sales
└── Dashboard (FastAPI + WebSocket) — live web UI
```

## Setup

```bash
# Install dependencies
uv sync

# Configure credentials (via environment variables)
export ALPACA_API_KEY=your_paper_key
export ALPACA_SECRET_KEY=your_paper_secret
export ANTHROPIC_API_KEY=your_key

# Optional: configure dashboard port (default: 8000)
export PORT=8080
```

## Usage

```bash
# Single analysis cycle (dry run, no trades)
uv run python -m auto_investor

# Single cycle with live execution (paper trading)
uv run python -m auto_investor --execute

# Run all day: scheduler + dashboard
uv run python -m auto_investor --schedule --execute

# Dashboard only (view past data, no trading)
uv run python -m auto_investor --dashboard

# Reset database and start fresh
uv run python -m auto_investor --reset
```

The scheduler runs every `interval_minutes`. Equities trade during configured market hours (Mon–Fri); crypto trades 24/7. The dashboard provides real-time updates via WebSocket.

## Configuration

Edit `config.yaml` to customize:

```yaml
trading:
  mode: paper                    # paper | live
  schedule:
    interval_minutes: 5          # cycle frequency
    market_open: "04:00"         # equity trading start (extended hours)
    market_close: "20:00"        # equity trading end (extended hours)

watchlist:                       # always-analyzed equity tickers
  - AAPL
  - MSFT
  - SPY
  # ... supplemented with dynamic top movers each cycle

crypto_watchlist:                # always-analyzed crypto pairs
  - BTC/USD
  - ETH/USD
  - SOL/USD
  - DOGE/USD
  # ... supplemented with top movers from all 22 Alpaca pairs

risk:
  max_position_pct: 25           # max % of portfolio in one position
  max_portfolio_risk_pct: 100    # max % of portfolio deployed
  daily_loss_limit_pct: 3        # circuit breaker: stop if down X% today
  max_trades_per_day: 1000       # max orders per day
  min_cash_reserve_pct: 0.0      # minimum cash reserve %
  low_price_threshold: 10.0      # equities below this get tighter limits
  low_price_max_position_pct: 3.0

ai:
  model: claude-sonnet-4-20250514
  max_tokens: 4096
  temperature: 0.3
```

## Dashboard

The web dashboard (`http://localhost:8000`) provides:

- **Header cards**: Total equity, cash, buying power, daily P&L, equity P&L, crypto P&L
- **Equity chart**: Intraday portfolio value with live updates
- **Position tables**: Separate equity and crypto holdings with real-time prices
- **Recent decisions**: AI reasoning with action badges, confidence, and risk veto status
- **Execution history**: Order status, fill prices, estimated amounts for pending orders
- **Symbol charts**: Click any symbol for a 30-day price chart

All data updates in real-time via WebSocket — no page refresh needed.

## Risk Management

- **Circuit breaker**: Halts trading if daily loss exceeds configured threshold
- **Position limits**: Max % of portfolio per position (tighter limits for low-priced equities)
- **Wash sale prevention**: 30-day cooldown after selling equities at a loss (not applied to crypto)
- **HOLD cooldown**: 20-minute cooldown between re-analyzing the same equity ticker (no cooldown for crypto)
- **Daily trade cap**: Configurable maximum orders per day
- **Cash reserve**: Optional minimum cash percentage

## Docker / Unraid

The Docker image is published to `ghcr.io/kenyonj/auto-investor:latest` and auto-built on every push to `main`.

```bash
docker run -d \
  -e ALPACA_API_KEY=your_paper_key \
  -e ALPACA_SECRET_KEY=your_paper_secret \
  -e ANTHROPIC_API_KEY=your_key \
  -v /path/to/data:/app/data \
  -v /path/to/config.yaml:/app/config.yaml \
  -p 8000:8000 \
  ghcr.io/kenyonj/auto-investor:latest
```

### Data Persistence

The SQLite database is stored at `/app/data/auto_investor.db` inside the container. **Mount a host directory to `/app/data`** to persist your trading history, decisions, and equity snapshots across container restarts and image updates.

On Unraid, the default host path is `/mnt/user/appdata/auto-investor` — this is pre-configured in the Community Apps template.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ALPACA_API_KEY` | Yes | Alpaca paper trading API key |
| `ALPACA_SECRET_KEY` | Yes | Alpaca paper trading secret key |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `PORT` | No | Dashboard port (default: `8000`) |
| `TZ` | No | Container timezone (default: `America/New_York`) |

## Safety

- All AI decisions are logged with full reasoning to SQLite
- Risk manager can veto any trade
- Circuit breaker on daily drawdown
- Paper trading by default — live trading requires explicit opt-in
- Crypto-specific AI guidelines: accept volatility, don't panic-sell dips
