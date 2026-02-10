# Copilot Instructions

## Build & Test

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_risk.py::test_name

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Single analysis cycle (dry run)
uv run python -m auto_investor

# Run with live execution (paper trading)
uv run python -m auto_investor --execute

# Run all day: scheduler + dashboard
uv run python -m auto_investor --schedule --execute

# Dashboard only (view past data)
uv run python -m auto_investor --dashboard

# Reset database
uv run python -m auto_investor --reset
```

## Architecture

The bot runs on a configurable interval. Equities trade during market hours (Mon–Fri); crypto trades 24/7:

1. **AlpacaClient** fetches portfolio snapshot, market quotes, price history, top movers (equities via screener, crypto via 24h % change scan across all 22 Alpaca-supported /USD pairs)
2. **ExecutionEngine** merges core watchlist with dynamic movers, applies HOLD cooldowns (20min for equities, none for crypto)
3. **AnalystAgent** sends portfolio + quotes + price history to Claude with asset-specific guidelines (crypto: accept volatility, don't panic-sell dips, let winners run)
4. **RiskManager** evaluates each decision against circuit breakers, position limits, and wash sale rules (wash sale skipped for crypto); vetoes unsafe trades by mutating them to HOLD with `risk_notes = "VETOED: ..."`
5. **ExecutionEngine** optionally submits orders via Alpaca
6. **DataStore** logs all decisions, executions, snapshots, and loss sales to SQLite
7. **Dashboard** (FastAPI + Jinja2 + WebSocket) provides real-time UI with split equity/crypto P&L cards, separate position tables, decision history, execution tracking, and symbol charts

Key data flow: `Portfolio → Quotes → AI Analysis → Risk Filter → Execution → Logging`

## Conventions

- **Config**: YAML-based (`config.yaml`) with Pydantic models. Secrets come from environment variables via `pydantic-settings` (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`). Dashboard port via `PORT` env var (default 8000).
- **Models**: All data structures use Pydantic `BaseModel`. Actions and confidence levels are string enums.
- **Type hints**: Python 3.12+ style, including `X | None` union syntax.
- **Risk vetoes**: RiskManager mutates rejected decisions in-place, converting them to HOLD with a VETOED prefix in `risk_notes`.
- **Crypto symbol formats**: Watchlist and decisions use `BTC/USD` (with slash). Alpaca positions return `BTCUSD` (no slash) — `AlpacaClient.normalize_symbol()` converts to slash format for display. `AlpacaClient.is_crypto()` recognizes both formats.
- **Asset class detection**: Use `asset_class` field (from Alpaca's `AssetClass` enum `.value`: `"crypto"`, `"us_equity"`) rather than checking for `/` in symbol when classifying positions.
- **Tests**: Unit tests use inline factory helpers (`_make_portfolio()`, `_make_decision()`) rather than shared fixtures. No mocking—tests operate on real Pydantic models and RiskManager logic.
- **Linting**: Ruff with rules `E, F, I, N, W, UP`, line length 100, targeting Python 3.12.
- **Styles**: CSS extracted to `dashboard/static/style.css`, served via FastAPI StaticFiles mount.
- **Number formatting**: Use `{:,.2f}` for dollar amounts (with commas), up to 4 decimal places for quantities (trailing zeros stripped). Times formatted as `YYYY-MM-DD HH:MM` (24h).
- **Dry run by default**: The bot only executes trades when `--execute` is passed. Always defaults to paper trading mode.
