"""Configuration models and loader."""

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class TradingSchedule(BaseModel):
    interval_minutes: int = 30
    market_open: str = "09:35"
    market_close: str = "15:55"


class TradingConfig(BaseModel):
    mode: str = "paper"
    schedule: TradingSchedule = TradingSchedule()


class RiskConfig(BaseModel):
    max_position_pct: float = 15.0
    max_portfolio_risk_pct: float = 80.0
    daily_loss_limit_pct: float = 3.0
    max_trades_per_day: int = 10
    min_cash_reserve_pct: float = 20.0
    session_budget: float | None = None  # max capital to deploy per session
    low_price_threshold: float = 10.0  # stocks below this price get tighter limits
    low_price_max_position_pct: float = 3.0  # max portfolio % for low-priced stocks


class AIConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.3


class AppConfig(BaseModel):
    trading: TradingConfig = TradingConfig()
    watchlist: list[str] = []
    crypto_watchlist: list[str] = []
    reddit_subreddits: list[str] = ["stocks", "investing", "algotrading"]
    risk: RiskConfig = RiskConfig()
    ai: AIConfig = AIConfig()


class Secrets(BaseSettings):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    anthropic_api_key: str = ""

    model_config = {"env_prefix": "", "case_sensitive": False}


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load application config from YAML file."""
    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return AppConfig(**data)
    return AppConfig()
