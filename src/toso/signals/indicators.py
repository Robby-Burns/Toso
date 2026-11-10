"""TOSO Technical Indicators.

Deterministic mathematical implementations of Moving Averages,
Donchian Breakout Channels, and Average True Range (ATR).
Free of external side-effects and floating-point non-determinism.
"""

from collections.abc import Sequence

from toso.signals.errors import InsufficientDataError
from toso.signals.models import Bar


def calculate_sma(prices: Sequence[float], period: int) -> float:
    """Calculate Simple Moving Average over the latest `period` values.

    Args:
        prices: Sequence of price floats.
        period: Number of periods for SMA window (must be > 0).

    Returns:
        Deterministic float SMA value.

    Raises:
        InsufficientDataError: If available prices are fewer than required period.
    """
    if period <= 0:
        raise ValueError(f"period must be strictly positive, got {period}")
    if len(prices) < period:
        raise InsufficientDataError(
            f"Insufficient data for SMA({period}): received {len(prices)} bars."
        )
    window = prices[-period:]
    return sum(window) / period


def calculate_donchian_channel(
    bars: Sequence[Bar], lookback: int, offset: int = 1
) -> tuple[float, float]:
    """Calculate Donchian Channel (highest high and lowest low) over prior bars.

    Args:
        bars: Sequence of Bar instances.
        lookback: Number of completed prior bars to inspect.
        offset: Offset from the end of the series (default 1 excludes current evaluating bar).

    Returns:
        Tuple of (highest_high, lowest_low).

    Raises:
        InsufficientDataError: If available bars are fewer than lookback + offset.
    """
    if lookback <= 0:
        raise ValueError(f"lookback must be strictly positive, got {lookback}")
    required_len = lookback + offset
    if len(bars) < required_len:
        raise InsufficientDataError(
            f"Insufficient data for Donchian Channel({lookback}): received {len(bars)} bars, "
            f"required {required_len}."
        )

    start_idx = len(bars) - required_len
    end_idx = len(bars) - offset
    window = bars[start_idx:end_idx]

    highest_high = max(bar.high for bar in window)
    lowest_low = min(bar.low for bar in window)
    return highest_high, lowest_low


def calculate_atr(bars: Sequence[Bar], period: int) -> float:
    """Calculate Average True Range (ATR) over the latest `period` bars.

    True Range for bar i:
        TR_i = max(high_i - low_i, abs(high_i - close_{i-1}), abs(low_i - close_{i-1}))

    Args:
        bars: Sequence of Bar instances.
        period: Number of periods to average (must be > 0).

    Returns:
        Deterministic ATR float value.

    Raises:
        InsufficientDataError: If available bars are fewer than period + 1.
    """
    if period <= 0:
        raise ValueError(f"period must be strictly positive, got {period}")
    required_bars = period + 1
    if len(bars) < required_bars:
        raise InsufficientDataError(
            f"Insufficient data for ATR({period}): received {len(bars)} bars, "
            f"required {required_bars}."
        )

    # Compute true ranges for the last `period` bars
    start_idx = len(bars) - period
    true_ranges: list[float] = []

    for i in range(start_idx, len(bars)):
        curr_bar = bars[i]
        prev_close = bars[i - 1].close
        h_l = curr_bar.high - curr_bar.low
        h_pc = abs(curr_bar.high - prev_close)
        l_pc = abs(curr_bar.low - prev_close)
        tr = max(h_l, h_pc, l_pc)
        true_ranges.append(tr)

    return sum(true_ranges) / period
