"""Technical indicators computed from daily bar data."""

from auto_investor.models import DailyBar


def compute_indicators(
    bars: dict[str, list[DailyBar]],
) -> dict[str, dict[str, str]]:
    """Compute technical indicators for each symbol.

    Returns a dict of symbol -> dict of indicator name -> formatted value.
    Requires at least 26 bars for MACD, 20 for Bollinger, 14 for RSI.
    """
    result: dict[str, dict[str, str]] = {}
    for symbol, symbol_bars in bars.items():
        closes = [b.close for b in symbol_bars]
        if len(closes) < 5:
            continue
        indicators: dict[str, str] = {}

        # Simple Moving Averages
        if len(closes) >= 10:
            sma10 = sum(closes[-10:]) / 10
            indicators["SMA_10"] = f"{sma10:.2f}"
        if len(closes) >= 20:
            sma20 = sum(closes[-20:]) / 20
            indicators["SMA_20"] = f"{sma20:.2f}"

        # RSI (14-period)
        if len(closes) >= 15:
            rsi = _rsi(closes, 14)
            indicators["RSI_14"] = f"{rsi:.1f}"
            if rsi > 70:
                indicators["RSI_signal"] = "OVERBOUGHT"
            elif rsi < 30:
                indicators["RSI_signal"] = "OVERSOLD"
            else:
                indicators["RSI_signal"] = "NEUTRAL"

        # MACD (12, 26, 9)
        if len(closes) >= 35:
            macd_line, signal_line, histogram = _macd(closes)
            indicators["MACD"] = f"{macd_line:.4f}"
            indicators["MACD_signal"] = f"{signal_line:.4f}"
            indicators["MACD_histogram"] = f"{histogram:.4f}"
            if histogram > 0:
                indicators["MACD_trend"] = "BULLISH"
            else:
                indicators["MACD_trend"] = "BEARISH"

        # Bollinger Bands (20-period, 2 std dev)
        if len(closes) >= 20:
            upper, middle, lower = _bollinger(closes, 20, 2)
            current = closes[-1]
            indicators["BB_upper"] = f"{upper:.2f}"
            indicators["BB_middle"] = f"{middle:.2f}"
            indicators["BB_lower"] = f"{lower:.2f}"
            if current > upper:
                indicators["BB_signal"] = "ABOVE_UPPER (overbought)"
            elif current < lower:
                indicators["BB_signal"] = "BELOW_LOWER (oversold)"
            else:
                pct = ((current - lower) / (upper - lower)) * 100
                indicators["BB_signal"] = f"IN_BAND ({pct:.0f}%)"

        if indicators:
            result[symbol] = indicators

    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    """Compute RSI using exponential moving average method."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float]:
    """Compute MACD line, signal line, and histogram."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    macd_series = [f - s for f, s in zip(ema_fast[-slow:], ema_slow)]
    signal_series = _ema(macd_series, signal)

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _ema(values: list[float], period: int) -> list[float]:
    """Compute exponential moving average."""
    if len(values) < period:
        return values
    multiplier = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for val in values[period:]:
        ema.append((val - ema[-1]) * multiplier + ema[-1])
    return ema


def _bollinger(
    closes: list[float],
    period: int = 20,
    num_std: int = 2,
) -> tuple[float, float, float]:
    """Compute Bollinger Bands (upper, middle, lower)."""
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance**0.5
    return middle + num_std * std, middle, middle - num_std * std
