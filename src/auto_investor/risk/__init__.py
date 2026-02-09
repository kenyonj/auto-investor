"""Risk management engine — guardrails for AI trade decisions."""

from auto_investor.config import RiskConfig
from auto_investor.models import Action, PortfolioSnapshot, TradeDecision


class RiskManager:
    """Evaluates and filters trade decisions based on risk rules."""

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.trades_today = 0

    def evaluate(
        self, decisions: list[TradeDecision], portfolio: PortfolioSnapshot
    ) -> list[TradeDecision]:
        """Filter decisions through risk checks. Returns approved decisions."""
        if self._circuit_breaker_triggered(portfolio):
            return []

        approved = []
        for decision in decisions:
            if decision.action == Action.HOLD:
                approved.append(decision)
                continue

            vetoed, reason = self._check_decision(decision, portfolio)
            if vetoed:
                decision.risk_notes = f"VETOED: {reason}"
                decision.action = Action.HOLD
                decision.quantity = None
            else:
                self.trades_today += 1

            approved.append(decision)

        return approved

    def _circuit_breaker_triggered(self, portfolio: PortfolioSnapshot) -> bool:
        """Stop all trading if daily loss exceeds limit."""
        if portfolio.daily_pl_pct < -self.config.daily_loss_limit_pct:
            return True
        if self.trades_today >= self.config.max_trades_per_day:
            return True
        return False

    def _check_decision(
        self, decision: TradeDecision, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        """Check a single decision against risk rules. Returns (vetoed, reason)."""
        if decision.action == Action.BUY:
            return self._check_buy(decision, portfolio)
        if decision.action == Action.SELL:
            return self._check_sell(decision, portfolio)
        return False, ""

    def _check_buy(
        self, decision: TradeDecision, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        """Validate a buy decision."""
        if not decision.quantity or decision.quantity <= 0:
            return True, "Invalid quantity"

        # Check cash reserve
        cash_pct = (portfolio.cash / portfolio.equity) * 100 if portfolio.equity else 0
        if cash_pct <= self.config.min_cash_reserve_pct:
            return True, f"Cash reserve too low ({cash_pct:.1f}% < {self.config.min_cash_reserve_pct}%)"

        # Check max position size
        existing = next((p for p in portfolio.positions if p.symbol == decision.symbol), None)
        existing_value = existing.market_value if existing else 0
        # Estimate new position value (rough — uses current price from positions or 0)
        est_price = existing.current_price if existing else 0
        if est_price > 0:
            new_value = existing_value + (decision.quantity * est_price)
            position_pct = (new_value / portfolio.equity) * 100
            if position_pct > self.config.max_position_pct:
                return True, f"Position too large ({position_pct:.1f}% > {self.config.max_position_pct}%)"

        return False, ""

    def _check_sell(
        self, decision: TradeDecision, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        """Validate a sell decision."""
        existing = next((p for p in portfolio.positions if p.symbol == decision.symbol), None)
        if not existing:
            return True, f"No position in {decision.symbol} to sell"
        if decision.quantity and decision.quantity > existing.quantity:
            return True, f"Sell qty ({decision.quantity}) > position ({existing.quantity})"
        return False, ""

    def reset_daily(self):
        """Reset daily counters (call at start of each trading day)."""
        self.trades_today = 0
