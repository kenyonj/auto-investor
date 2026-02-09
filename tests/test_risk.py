"""Tests for risk manager."""

from auto_investor.models import Action, Confidence, Position, PortfolioSnapshot, TradeDecision
from auto_investor.risk import RiskManager


def _make_portfolio(equity=100000, cash=50000, positions=None):
    return PortfolioSnapshot(
        equity=equity,
        cash=cash,
        buying_power=cash,
        positions=positions or [],
        daily_pl=0,
        daily_pl_pct=0,
    )


def _make_decision(symbol="AAPL", action=Action.BUY, quantity=10):
    return TradeDecision(
        symbol=symbol,
        action=action,
        confidence=Confidence.MEDIUM,
        quantity=quantity,
        reasoning="Test",
    )


def test_circuit_breaker_on_daily_loss():
    rm = RiskManager()
    portfolio = _make_portfolio()
    portfolio.daily_pl_pct = -5.0  # Exceeds 3% default limit

    decisions = [_make_decision()]
    result = rm.evaluate(decisions, portfolio)
    assert result == []


def test_circuit_breaker_on_max_trades():
    rm = RiskManager()
    rm.trades_today = 10  # At limit
    portfolio = _make_portfolio()

    decisions = [_make_decision()]
    result = rm.evaluate(decisions, portfolio)
    assert result == []


def test_buy_approved_when_within_limits():
    rm = RiskManager()
    portfolio = _make_portfolio(equity=100000, cash=50000)

    decisions = [_make_decision(quantity=5)]
    result = rm.evaluate(decisions, portfolio)
    assert len(result) == 1
    assert result[0].action == Action.BUY


def test_sell_vetoed_when_no_position():
    rm = RiskManager()
    portfolio = _make_portfolio()

    decisions = [_make_decision(action=Action.SELL, quantity=5)]
    result = rm.evaluate(decisions, portfolio)
    assert len(result) == 1
    assert result[0].action == Action.HOLD
    assert "VETOED" in result[0].risk_notes


def test_hold_passes_through():
    rm = RiskManager()
    portfolio = _make_portfolio()

    decisions = [_make_decision(action=Action.HOLD, quantity=None)]
    result = rm.evaluate(decisions, portfolio)
    assert len(result) == 1
    assert result[0].action == Action.HOLD


def test_low_cash_vetoes_buy():
    rm = RiskManager()
    portfolio = _make_portfolio(equity=100000, cash=10000)  # 10% cash, below 20% reserve

    decisions = [_make_decision(quantity=5)]
    result = rm.evaluate(decisions, portfolio)
    assert len(result) == 1
    assert result[0].action == Action.HOLD
    assert "VETOED" in result[0].risk_notes
