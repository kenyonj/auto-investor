"""Risk management engine — guardrails for AI trade decisions."""

from datetime import datetime, timedelta

from auto_investor.config import RiskConfig
from auto_investor.models import Action, PortfolioSnapshot, TradeDecision

WASH_SALE_DAYS = 30
MIN_HOLD_MINUTES = 30


class RiskManager:
    """Evaluates and filters trade decisions based on risk rules."""

    def __init__(self, config: RiskConfig | None = None, store=None):
        self.config = config or RiskConfig()
        self.trades_today = 0
        self.session_spent = 0.0
        self.store = store  # DataStore for wash sale lookups

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
                # Track session budget spending for buys
                if decision.action == Action.BUY and decision.quantity:
                    existing = next(
                        (p for p in portfolio.positions if p.symbol == decision.symbol), None
                    )
                    price = existing.current_price if existing else 0
                    if price > 0:
                        self.session_spent += decision.quantity * price

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

    def _check_buy(self, decision: TradeDecision, portfolio: PortfolioSnapshot) -> tuple[bool, str]:
        """Validate a buy decision."""
        if not decision.quantity or decision.quantity <= 0:
            return True, "Invalid quantity"

        # Check wash sale rule (not applicable to crypto)
        if self.store and "/" not in decision.symbol:
            loss_sale = self.store.get_recent_loss_sale(decision.symbol, WASH_SALE_DAYS)
            if loss_sale:
                return (
                    True,
                    f"Wash sale: {decision.symbol} sold at loss "
                    f"(${loss_sale['loss']:,.2f}) on {loss_sale['timestamp'][:10]}, "
                    f"{WASH_SALE_DAYS}-day cooldown",
                )

        # Check session budget
        if self.config.session_budget is not None:
            existing = next((p for p in portfolio.positions if p.symbol == decision.symbol), None)
            est_price = existing.current_price if existing else 0
            if est_price > 0:
                cost = decision.quantity * est_price
                if self.session_spent + cost > self.config.session_budget:
                    remaining = self.config.session_budget - self.session_spent
                    return (
                        True,
                        f"Exceeds session budget "
                        f"(${cost:,.0f} would exceed ${remaining:,.0f} remaining)",
                    )

        # Check buying power reserve
        bp_pct = (portfolio.buying_power / portfolio.equity) * 100 if portfolio.equity else 0
        if bp_pct <= self.config.min_cash_reserve_pct:
            return (
                True,
                f"Buying power too low ({bp_pct:.1f}% < {self.config.min_cash_reserve_pct}%)",
            )

        # Check max position size (tighter limit for low-priced stocks, skip for crypto)
        existing = next((p for p in portfolio.positions if p.symbol == decision.symbol), None)
        existing_value = existing.market_value if existing else 0
        est_price = existing.current_price if existing else 0
        is_crypto = "/" in decision.symbol
        if est_price > 0:
            new_value = existing_value + (decision.quantity * est_price)
            position_pct = (new_value / portfolio.equity) * 100
            max_pct = self.config.max_position_pct
            if not is_crypto and est_price < self.config.low_price_threshold:
                max_pct = self.config.low_price_max_position_pct
            if position_pct > max_pct:
                label = "low-priced " if est_price < self.config.low_price_threshold else ""
                return (
                    True,
                    f"Position too large for {label}{'crypto' if is_crypto else 'stock'} "
                    f"({position_pct:.1f}% > {max_pct}%)",
                )

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

        # Minimum hold period: don't sell within MIN_HOLD_MINUTES of buying
        if self.store:
            last_buy = self.store.get_last_buy_time(decision.symbol)
            if last_buy:
                held_for = datetime.now() - last_buy
                if held_for < timedelta(minutes=MIN_HOLD_MINUTES):
                    mins_left = MIN_HOLD_MINUTES - int(held_for.total_seconds() / 60)
                    return (
                        True,
                        f"Min hold period: bought {int(held_for.total_seconds() / 60)}m ago, "
                        f"wait {mins_left}m more",
                    )

        return False, ""

    def reset_daily(self):
        """Reset daily counters (call at start of each trading day)."""
        self.trades_today = 0
