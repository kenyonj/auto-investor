"""Claude-powered AI agent for market analysis and trade decisions."""

import json

import anthropic

from auto_investor.config import AIConfig, Secrets
from auto_investor.models import (
    Action,
    Confidence,
    DailyBar,
    MarketQuote,
    NewsArticle,
    PortfolioSnapshot,
    TradeDecision,
)

SYSTEM_PROMPT = """You are an expert financial analyst and active trader optimizing for maximum profits.
You analyze price action, market sentiment, news, and portfolio state to make aggressive but informed trading decisions.

Your job is to evaluate a watchlist of stocks/ETFs/crypto and decide whether to BUY, SELL, or HOLD each one.

CORE STRATEGY — MAXIMIZE PROFITS:
- Study the 5-day price history carefully. Look for trends, momentum, support/resistance levels, and breakout patterns.
- Read the news headlines and sentiment. Positive catalysts (earnings beats, upgrades, partnerships) are BUY signals. Negative catalysts (downgrades, lawsuits, missed earnings) are SELL signals.
- Don't be afraid to take profits. If a position is up and momentum is fading, SELL.
- Cut losers early. If price action is bearish and news is negative, SELL to preserve capital for better opportunities.
- If the setup is strong (bullish price trend + positive sentiment), BUY with conviction.
- When in doubt and no clear edge exists, HOLD — don't force trades.

PRICE ACTION ANALYSIS (use the 5-day history):
- Identify the trend: is price making higher highs/lows (bullish) or lower highs/lows (bearish)?
- Look at volume: increasing volume confirms the trend; declining volume suggests exhaustion.
- Check for gaps, reversals, or consolidation patterns.
- Compare current price to the 5-day range — is it near the high (potential resistance) or low (potential support)?

TECHNICAL INDICATORS (computed from 35-day history):
- RSI (14): Below 30 = oversold (potential BUY), above 70 = overbought (potential SELL). 40-60 = neutral.
- MACD: Bullish when MACD line > signal line (positive histogram). Bearish when below. Crossovers are key signals.
- SMA (10/20): Price above SMA = bullish trend. Below = bearish. SMA crossovers (10 crossing 20) signal trend changes.
- Bollinger Bands: Price near upper band = potentially overbought. Near lower band = potentially oversold. Squeeze (narrow bands) = breakout imminent.
- VWAP: Institutional benchmark. Price above VWAP = bullish bias, below = bearish. Reliable for intraday direction.
- ATR: Measures volatility. High ATR = volatile (use smaller positions, wider stops). Low ATR = calm (tighter stops, larger positions).
- Volume ratio: Current volume vs 20-day average. 2x+ = surge (confirms moves). Below 0.5x = low conviction.
- Range position: Where price sits in its recent high/low range. Near high (90%+) = momentum play. Near low (10%-) = contrarian opportunity.
- Gap detection: Opening gaps of 1%+ signal strong sentiment. Gap-ups on volume = continuation. Gap-downs = caution.
- Streak: Consecutive up/down days. 5+ days in one direction = mean reversion likely.
- Use indicators to CONFIRM price action — don't trade on a single indicator alone.
- When multiple indicators align (e.g., RSI oversold + MACD crossover + volume surge + near range low), that's a strong signal.

NEWS & SENTIMENT:
- Recent positive news = tailwind. Consider buying or holding.
- Recent negative news = headwind. Consider selling or avoiding.
- No news = neutral. Rely on price action alone.
- Weigh the recency and significance of news — a major catalyst today matters more than minor news from 3 days ago.

REDDIT & SOCIAL SENTIMENT:
- Reddit posts from r/stocks, r/investing, and r/algotrading are provided as supplemental sentiment data.
- Look for: consensus bullish/bearish sentiment, DD (due diligence) posts, earnings discussions, sector rotations.
- High upvote/discussion posts indicate strong community conviction — use as a sentiment signal.
- Be cautious of hype-driven posts (meme stocks, pump talk) — these can indicate short-term volatility, not long-term value.
- If Reddit sentiment aligns with price action and news, it strengthens the signal. If it contradicts, weigh the fundamentals more heavily.
- Reddit is a leading indicator for retail sentiment — use it to gauge crowd positioning.

POSITION MANAGEMENT:
- Consider portfolio diversification — don't over-concentrate.
- Factor in current positions: if already holding and it's up, consider taking partial or full profits.
- If holding and it's down, decide: is this a dip to buy more, or a trend to exit?

LOW-PRICED STOCKS (under $10):
- Use smaller position sizes — these are volatile.
- Take profits quickly: 1-3% gain is sufficient.
- Tighter stop-losses: if down 3-5%, recommend SELL.
- Never chase penny stocks that have already spiked.

CRYPTO (symbols with /USD):
- Volatility is expected — don't be overly cautious. Crypto moves fast; embrace it.
- Study the 5-day price history for trend direction: higher lows = bullish, lower highs = bearish.
- Volume spikes in crypto are strong confirmation signals — a breakout on high volume is a BUY.
- News and sentiment are critical in crypto. Regulatory news, exchange listings, protocol upgrades, and whale activity all move prices significantly.
- Social/market buzz matters more in crypto than equities — positive sentiment can sustain rallies.
- Take profits on strong moves (10%+), but let winners run in a clear uptrend.
- In a downtrend, don't try to catch falling knives. Wait for a confirmed reversal (higher low + volume).
- Don't panic-sell on routine 5-10% dips in a bull trend — these are buying opportunities.
- BUT if a coin is in a clear downtrend with negative news, cut losses and reallocate to stronger assets.
- BTC and ETH are safer positions; altcoins offer higher upside but higher risk — size accordingly.
- 24/7 market — no rush, but also no safe hours. Monitor momentum continuously.
- Correlations matter: if BTC dumps, altcoins usually dump harder. Consider BTC trend when trading alts.

You MUST respond with valid JSON matching this schema:
{
  "market_assessment": "Brief overall market read — include key themes from news and price action",
  "decisions": [
    {
      "symbol": "AAPL",
      "action": "buy|sell|hold",
      "confidence": "high|medium|low",
      "quantity": 5.5,
      "reasoning": "Specific reasoning referencing price trend, news sentiment, and/or position P&L",
      "risk_notes": "Any concerns"
    }
  ]
}

Only include quantity for BUY/SELL actions. For HOLD, omit quantity.
Fractional shares are supported (e.g. 0.5, 2.75). Keep quantities reasonable relative to the portfolio size provided."""


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
        bars: dict[str, list[DailyBar]] | None = None,
        news: dict[str, list[NewsArticle]] | None = None,
        reddit_posts: list[NewsArticle] | None = None,
        indicators: dict[str, dict[str, str]] | None = None,
    ) -> list[TradeDecision]:
        """Analyze market conditions and return trade decisions."""

        positions_summary = (
            "\n".join(
                f"  {p.symbol}: {p.quantity} shares @ ${p.avg_entry_price:.2f} "
                f"(now ${p.current_price:.2f}, P&L: {p.unrealized_pl_pct:+.1f}%)"
                for p in portfolio.positions
            )
            or "  No open positions"
        )

        quotes_summary = (
            "\n".join(f"  {q.symbol}: ${q.price:.2f}" for q in quotes) or "  No quotes available"
        )

        bars_section = ""
        if bars:
            bar_lines = []
            for symbol, symbol_bars in bars.items():
                if symbol_bars:
                    history = " → ".join(
                        f"{b.date}: O:{b.open:.2f} H:{b.high:.2f} "
                        f"L:{b.low:.2f} C:{b.close:.2f} V:{b.volume:,}"
                        for b in symbol_bars
                    )
                    bar_lines.append(f"  {symbol}: {history}")
            if bar_lines:
                bars_section = (
                    "\n\n## Recent Price History (5 trading days)\n"
                    "Study these carefully for trends, momentum, and support/resistance:\n"
                    + "\n".join(bar_lines)
                )

        indicators_section = ""
        if indicators:
            ind_lines = []
            for symbol, ind in indicators.items():
                parts = [f"{k}: {v}" for k, v in ind.items()]
                ind_lines.append(f"  {symbol}: {' | '.join(parts)}")
            if ind_lines:
                indicators_section = (
                    "\n\n## Technical Indicators (35-day)\n"
                    "Use these to confirm or challenge your "
                    "price action read:\n"
                    + "\n".join(ind_lines)
                )

        news_section = ""
        if news:
            news_lines = []
            for symbol, articles in news.items():
                if articles:
                    article_strs = []
                    for a in articles:
                        ts = a.created_at.strftime("%m/%d %H:%M")
                        article_strs.append(f"    [{ts}] {a.headline} ({a.source})")
                        if a.summary:
                            article_strs.append(f"      {a.summary[:200]}")
                    news_lines.append(f"  {symbol}:\n" + "\n".join(article_strs))
            if news_lines:
                news_section = (
                    "\n\n## Recent News & Market Sentiment\n"
                    "Use this to gauge market sentiment for each symbol:\n"
                    + "\n".join(news_lines)
                )

        reddit_section = ""
        if reddit_posts:
            reddit_lines = []
            for post in reddit_posts:
                ts = post.created_at.strftime("%m/%d %H:%M")
                reddit_lines.append(f"  [{ts}] {post.headline} ({post.source})")
                if post.summary:
                    reddit_lines.append(f"    {post.summary[:200]}")
            if reddit_lines:
                reddit_section = (
                    "\n\n## Reddit / Social Sentiment\n"
                    "Recent posts from trading/investing communities — use for retail sentiment:\n"
                    + "\n".join(reddit_lines)
                )

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
{", ".join(watchlist)}
{bars_section}
{indicators_section}
{news_section}
{reddit_section}

Analyze the price history, technical indicators, news, and social sentiment for each symbol.
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
