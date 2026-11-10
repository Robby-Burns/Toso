"""TOSO Deterministic Signal Generator.

Evaluates market data bar series against approved StrategyConfig parameters
to produce deterministic, reproducible trade signals without LLM interpretation.
"""

from toso.config.models import StrategyConfig
from toso.signals.errors import InsufficientDataError
from toso.signals.indicators import calculate_atr, calculate_donchian_channel, calculate_sma
from toso.signals.models import (
    BarSeries,
    SignalIndicatorSnapshot,
    SignalType,
    TradeSignal,
)


class DeterministicSignalGenerator:
    """Generates deterministic trading signals from BarSeries and StrategyConfig."""

    def __init__(self, config: StrategyConfig) -> None:
        """Initialize generator with an approved StrategyConfig."""
        self.config = config

    def required_bar_count(self) -> int:
        """Return the minimum number of bars required to evaluate all indicators."""
        return max(
            self.config.slow_ma_period,
            self.config.breakout_lookback_bars + 1,
            self.config.atr_period + 1,
        )

    def generate(self, series: BarSeries) -> TradeSignal:
        """Evaluate market data series and produce deterministic TradeSignal.

        Args:
            series: Chronologically ordered BarSeries.

        Returns:
            Deterministic TradeSignal.

        Raises:
            InsufficientDataError: If series has fewer bars than required_bar_count().
        """
        req_count = self.required_bar_count()
        if len(series) < req_count:
            raise InsufficientDataError(
                f"BarSeries for {series.symbol} contains {len(series)} bars, "
                f"but {req_count} bars are required for deterministic indicator evaluation."
            )

        latest_bar = series.latest_bar
        current_close = latest_bar.close

        # Deterministic indicator calculations
        fast_ma = calculate_sma(series.closes(), self.config.fast_ma_period)
        slow_ma = calculate_sma(series.closes(), self.config.slow_ma_period)
        atr = calculate_atr(series.bars, self.config.atr_period)
        donchian_high, donchian_low = calculate_donchian_channel(
            series.bars, self.config.breakout_lookback_bars, offset=1
        )

        indicators = SignalIndicatorSnapshot(
            fast_ma=fast_ma,
            slow_ma=slow_ma,
            atr=atr,
            donchian_high=donchian_high,
            donchian_low=donchian_low,
            current_close=current_close,
        )

        is_bullish_trend = (fast_ma > slow_ma) and (current_close > fast_ma)
        is_breakout_high = current_close > donchian_high
        is_breakdown_low = current_close < donchian_low
        is_trend_broken = fast_ma < slow_ma or is_breakdown_low

        if is_bullish_trend and is_breakout_high:
            invalidation_price = current_close - (self.config.invalidation_atr_multiple * atr)
            return TradeSignal(
                symbol=series.symbol,
                timestamp=latest_bar.timestamp,
                signal_type=SignalType.BULLISH_ENTRY,
                price=current_close,
                invalidation_price=invalidation_price,
                indicators=indicators,
                reason=(
                    f"Bullish trend confirmed (Fast MA {fast_ma:.2f} > Slow MA {slow_ma:.2f}) "
                    f"and new {self.config.breakout_lookback_bars}-bar Donchian breakout "
                    f"({current_close:.2f} > {donchian_high:.2f})."
                ),
            )

        if is_trend_broken:
            return TradeSignal(
                symbol=series.symbol,
                timestamp=latest_bar.timestamp,
                signal_type=SignalType.BEARISH_EXIT,
                price=current_close,
                invalidation_price=None,
                indicators=indicators,
                reason=(
                    f"Trend invalidation triggered: close ({current_close:.2f}) "
                    f"broke lower channel ({donchian_low:.2f}) or moving averages crossed bearish."
                ),
            )

        if is_bullish_trend:
            invalidation_price = current_close - (self.config.invalidation_atr_multiple * atr)
            return TradeSignal(
                symbol=series.symbol,
                timestamp=latest_bar.timestamp,
                signal_type=SignalType.HOLD,
                price=current_close,
                invalidation_price=invalidation_price,
                indicators=indicators,
                reason="Bullish trend active without new breakout.",
            )

        return TradeSignal(
            symbol=series.symbol,
            timestamp=latest_bar.timestamp,
            signal_type=SignalType.NO_SIGNAL,
            price=current_close,
            invalidation_price=None,
            indicators=indicators,
            reason="No deterministic trend detected.",
        )
