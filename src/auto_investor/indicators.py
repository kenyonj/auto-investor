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

        # --- Rule-based indicators ---

        highs = [b.high for b in symbol_bars]
        lows = [b.low for b in symbol_bars]
        volumes = [b.volume for b in symbol_bars]

        # VWAP approximation (sum of typical_price * volume / sum of volume)
        if len(symbol_bars) >= 5:
            tp_vol = sum(
                ((b.high + b.low + b.close) / 3) * b.volume
                for b in symbol_bars[-20:] if b.volume > 0
            )
            total_vol = sum(
                b.volume for b in symbol_bars[-20:] if b.volume > 0
            )
            if total_vol > 0:
                vwap = tp_vol / total_vol
                indicators["VWAP"] = f"{vwap:.2f}"
                if closes[-1] > vwap:
                    indicators["VWAP_signal"] = "ABOVE (bullish)"
                else:
                    indicators["VWAP_signal"] = "BELOW (bearish)"

        # ATR (14-period Average True Range)
        if len(symbol_bars) >= 15:
            atr = _atr(symbol_bars, 14)
            indicators["ATR_14"] = f"{atr:.2f}"
            atr_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 else 0
            indicators["ATR_pct"] = f"{atr_pct:.1f}%"
            if atr_pct > 5:
                indicators["volatility"] = "HIGH"
            elif atr_pct > 2:
                indicators["volatility"] = "MODERATE"
            else:
                indicators["volatility"] = "LOW"

        # Volume anomaly (current vs 20-day average)
        if len(volumes) >= 20 and volumes[-1] > 0:
            avg_vol = sum(volumes[-20:]) / 20
            if avg_vol > 0:
                vol_ratio = volumes[-1] / avg_vol
                indicators["vol_ratio"] = f"{vol_ratio:.1f}x"
                if vol_ratio >= 2.0:
                    indicators["vol_signal"] = "SURGE (2x+ avg)"
                elif vol_ratio >= 1.5:
                    indicators["vol_signal"] = "ELEVATED"
                elif vol_ratio <= 0.5:
                    indicators["vol_signal"] = "DRY (low interest)"
                else:
                    indicators["vol_signal"] = "NORMAL"

        # 52-week high/low proximity (using available bars)
        if len(symbol_bars) >= 20:
            high_max = max(highs)
            low_min = min(lows)
            current = closes[-1]
            range_span = high_max - low_min
            if range_span > 0:
                pct_of_range = (
                    (current - low_min) / range_span
                ) * 100
                indicators["range_position"] = f"{pct_of_range:.0f}%"
                if pct_of_range >= 90:
                    indicators["range_signal"] = "NEAR HIGH (momentum)"
                elif pct_of_range <= 10:
                    indicators["range_signal"] = "NEAR LOW (reversal?)"
                else:
                    indicators["range_signal"] = "MID-RANGE"

        # Gap detection (last bar vs previous close)
        if len(symbol_bars) >= 2:
            prev_close = symbol_bars[-2].close
            today_open = symbol_bars[-1].open
            gap_pct = (
                ((today_open - prev_close) / prev_close) * 100
                if prev_close > 0 else 0
            )
            if abs(gap_pct) >= 1.0:
                direction = "UP" if gap_pct > 0 else "DOWN"
                indicators["gap"] = f"{direction} {abs(gap_pct):.1f}%"

        # Consecutive trend counter
        if len(closes) >= 2:
            streak = _streak(closes)
            if abs(streak) >= 2:
                direction = "UP" if streak > 0 else "DOWN"
                indicators["streak"] = (
                    f"{abs(streak)} days {direction}"
                )
                if abs(streak) >= 5:
                    indicators["streak_signal"] = "REVERSAL LIKELY"

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


def _atr(bars: list[DailyBar], period: int = 14) -> float:
    """Compute Average True Range."""
    true_ranges = []
    for i in range(1, len(bars)):
        high_low = bars[i].high - bars[i].low
        high_prev = abs(bars[i].high - bars[i - 1].close)
        low_prev = abs(bars[i].low - bars[i - 1].close)
        true_ranges.append(max(high_low, high_prev, low_prev))
    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _streak(closes: list[float]) -> int:
    """Count consecutive up or down days. Positive = up, negative = down."""
    if len(closes) < 2:
        return 0
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            if streak < 0:
                break
            streak += 1
        elif closes[i] < closes[i - 1]:
            if streak > 0:
                break
            streak -= 1
        else:
            break
    return streak
