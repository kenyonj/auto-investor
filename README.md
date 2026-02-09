# auto-investor

AI-driven automated trading bot using Claude + Alpaca.

## Overview

An AI agent that analyzes market conditions, researches stocks/ETFs, and executes trades automatically via Alpaca's API. Claude provides the analytical reasoning; risk management guardrails keep things safe.

**⚠️ Paper trading only until explicitly promoted to live.**

## Architecture

```
Orchestrator (scheduler)
├── Market Data Feed (Alpaca)
├── AI Agent (Claude) — analyze, research, decide
├── Risk Manager — position limits, drawdown protection
├── Execution Engine — Alpaca orders
└── Data Store (SQLite) — trades, decisions, snapshots
```

## Setup

```bash
# Install dependencies
uv sync

# Configure credentials (via 1Password or .env)
export ALPACA_API_KEY=your_paper_key
export ALPACA_SECRET_KEY=your_paper_secret
export ANTHROPIC_API_KEY=your_key

# Run (paper trading)
uv run python -m auto_investor
```

## Configuration

Edit `config.yaml` to customize:
- Watchlist (tickers to monitor)
- Risk parameters (max position size, daily loss limit)
- Trading schedule
- AI model preferences

## Safety

- All AI decisions are logged with full reasoning
- Risk manager can veto any trade
- Circuit breaker on daily drawdown
- Paper trading by default — live trading requires explicit opt-in
