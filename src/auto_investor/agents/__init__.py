"""Claude-powered AI agent for market analysis and trade decisions."""

import json

import anthropic

from auto_investor.config import AIConfig, Secrets
from auto_investor.models import Action, Confidence, MarketQuote, PortfolioSnapshot, TradeDecision

SYSTEM_PROMPT = """You are an expert financial analyst and trader. You analyze market conditions, 
portfolio state, and individual stock data to make informed trading decisions.

Your job is to evaluate a watchlist of stocks/ETFs and decide whether to BUY, SELL, or HOLD each one.

Guidelines:
- Be conservative by default. Only recommend BUY with high confidence when the setup is compelling.
- Consider portfolio diversification — don't over-concentrate.
- Factor in current positions when deciding (don't double down recklessly).
- Consider market conditions (trend, volatility, sector rotation).
- Provide clear, concise reasoning for every decision.
- Flag any risk concerns.

You MUST respond with valid JSON matching this schema:
{
  "market_assessment": "Brief overall market read",
  "decisions": [
    {
      "symbol": "AAPL",
      "action": "buy|sell|hold",
      "confidence": "high|medium|low",
      "quantity": 5,
      "reasoning": "Why this action",
      "risk_notes": "Any concerns"
    }
  ]
}

Only include quantity for BUY/SELL actions. For HOLD, omit quantity.
Keep quantities reasonable relative to the portfolio size provided."""


class AnalystAgent:
    """AI-powered market analyst that produces trade decisions."""

    def __init__(self, ai_config: AIConfig | None = None, secrets: Secrets | None = None):
        self.secrets = secrets or Secrets()
        self.config = ai_config or AIConfig()
        self.client = anthropic.Anthropic(api_key=self.secrets.anthropic_api_key)

    def analyze(
        self,
        portfolio: PortfolioSnapshot,
        quotes: list[MarketQuote],
        watchlist: list[str],
    ) -> list[TradeDecision]:
        """Analyze market conditions and return trade decisions."""

        positions_summary = "\n".join(
            f"  {p.symbol}: {p.quantity} shares @ ${p.avg_entry_price:.2f} "
            f"(now ${p.current_price:.2f}, P&L: {p.unrealized_pl_pct:+.1f}%)"
            for p in portfolio.positions
        ) or "  No open positions"

        quotes_summary = "\n".join(
            f"  {q.symbol}: ${q.price:.2f}" for q in quotes
        ) or "  No quotes available"

        prompt = f"""Analyze the following portfolio and market data, then provide trade decisions 
for each symbol in the watchlist.

## Portfolio State
- Equity: ${portfolio.equity:,.2f}
- Cash: ${portfolio.cash:,.2f}
- Buying Power: ${portfolio.buying_power:,.2f}
- Daily P&L: ${portfolio.daily_pl:+,.2f} ({portfolio.daily_pl_pct:+.2f}%)

## Current Positions
{positions_summary}

## Latest Quotes
{quotes_summary}

## Watchlist
{', '.join(watchlist)}

Provide your analysis and trade decisions as JSON."""

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        return self._parse_response(response.content[0].text)

    def _parse_response(self, text: str) -> list[TradeDecision]:
        """Parse the AI response into structured trade decisions."""
        # Extract JSON from response (handle markdown code blocks)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]

        data = json.loads(cleaned)
        decisions = []
        for d in data.get("decisions", []):
            decisions.append(
                TradeDecision(
                    symbol=d["symbol"],
                    action=Action(d["action"]),
                    confidence=Confidence(d["confidence"]),
                    quantity=d.get("quantity"),
                    reasoning=d["reasoning"],
                    risk_notes=d.get("risk_notes", ""),
                )
            )
        return decisions
