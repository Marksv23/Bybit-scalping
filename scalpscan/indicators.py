"""Volatility helpers operating on lists of Bar (oldest first)."""
from __future__ import annotations

from statistics import mean, median

from .models import Bar


def closed(bars: list[Bar]) -> list[Bar]:
    """Drop the last (still forming) bar."""
    return bars[:-1] if len(bars) > 1 else []


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Wilder ATR over closed bars."""
    bars = closed(bars)
    if len(bars) < period + 1:
        return None
    trs = [
        max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close))
        for prev, b in zip(bars, bars[1:])
    ]
    value = mean(trs[:period])
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def avg_range(bars: list[Bar], n: int) -> float | None:
    """Mean high-low of the last n closed bars."""
    bars = closed(bars)[-n:]
    if not bars:
        return None
    return mean(b.high - b.low for b in bars)


def median_range(bars: list[Bar], n: int) -> float | None:
    bars = closed(bars)[-n:]
    if not bars:
        return None
    return median(b.high - b.low for b in bars)


def median_bar_spread_points(bars: list[Bar], n: int) -> float | None:
    bars = [b for b in closed(bars)[-n:] if b.spread_points > 0]
    if not bars:
        return None
    return median(b.spread_points for b in bars)
