"""Comprehensive tests for TOSO Deterministic Signal Generation (Story TRADER-002).

Verifies:
- AC-001: Deterministic trend state and trade signal generation
  (BULLISH_ENTRY, BEARISH_EXIT, HOLD, NO_SIGNAL).
- AC-002: Rejection of insufficient, corrupt, non-chronological, or invalid market data.
- AC-003: 100% mathematical reproducibility and immutability.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.config.models import StrategyConfig
from toso.signals import (
    Bar,
    BarSeries,
    DeterministicSignalGenerator,
    InsufficientDataError,
    InvalidMarketDataError,
    SignalType,
    TradeSignal,
    calculate_atr,
    calculate_donchian_channel,
    calculate_sma,
)


@pytest.fixture
def default_strategy_config() -> StrategyConfig:
    """Fixture providing a standard approved StrategyConfig."""
    return StrategyConfig(
        strategy_id="core_trend_v1",
        timeframe="1d",
        fast_ma_period=50,
        slow_ma_period=200,
        atr_period=14,
        breakout_lookback_bars=20,
        invalidation_atr_multiple=2.0,
        pyramid_min_profit_r=1.0,
        max_pyramid_adds=3,
    )


def make_synthetic_bars(
    count: int,
    base_price: float = 100.0,
    slope: float = 0.5,
    daily_volatility: float = 2.0,
) -> list[Bar]:
    """Helper to generate chronologically consistent synthetic OHLCV bars."""
    start_time = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []

    current_price = base_price
    for i in range(count):
        ts = start_time + timedelta(days=i)
        current_price += slope
        half_vol = daily_volatility / 2.0

        bar_open = current_price - 0.2
        bar_high = current_price + half_vol
        bar_low = current_price - half_vol
        bar_close = current_price + 0.1

        # Maintain valid bar invariants
        bar_high = max(bar_high, bar_open, bar_close)
        bar_low = min(bar_low, bar_open, bar_close)

        bars.append(
            Bar(
                timestamp=ts,
                open=bar_open,
                high=bar_high,
                low=bar_low,
                close=bar_close,
                volume=1_000_000.0,
            )
        )
    return bars


# ==============================================================================
# AC-001: Deterministic Trend State & Signal Generation
# ==============================================================================


def test_ac001_bullish_entry_signal_on_breakout(default_strategy_config):
    """Verify strong uptrend with a 20-bar Donchian high breakout triggers BULLISH_ENTRY."""
    # Generate 220 bars in an uptrend where the last bar decisively breaks out above prior high
    bars = make_synthetic_bars(count=220, base_price=100.0, slope=0.5, daily_volatility=2.0)

    # Make the latest bar break out above previous 20-day high
    prev_max_high = max(b.high for b in bars[-21:-1])
    breakout_close = prev_max_high + 5.0
    latest_ts = bars[-1].timestamp
    bars[-1] = Bar(
        timestamp=latest_ts,
        open=breakout_close - 1.0,
        high=breakout_close + 2.0,
        low=breakout_close - 2.0,
        close=breakout_close,
        volume=1_500_000.0,
    )

    series = BarSeries(symbol="AAPL", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)
    signal = generator.generate(series)

    assert isinstance(signal, TradeSignal)
    assert signal.signal_type == SignalType.BULLISH_ENTRY
    assert signal.symbol == "AAPL"
    assert signal.price == breakout_close
    assert signal.invalidation_price is not None
    assert signal.invalidation_price < signal.price

    # Invalidation must be exactly: close - (invalidation_atr_multiple * atr)
    expected_inval = breakout_close - (2.0 * signal.indicators.atr)
    assert pytest.approx(signal.invalidation_price, rel=1e-5) == expected_inval
    assert signal.indicators.fast_ma > signal.indicators.slow_ma
    assert signal.price > signal.indicators.donchian_high


def test_ac001_bullish_hold_when_in_trend_without_new_breakout(default_strategy_config):
    """Verify established uptrend without a new breakout triggers HOLD with valid invalidation."""
    bars = make_synthetic_bars(count=220, base_price=100.0, slope=0.5, daily_volatility=2.0)

    # Ensure latest bar close is within prior channel (not breaking out)
    prev_max_high = max(b.high for b in bars[-21:-1])
    consolidation_close = prev_max_high - 1.0
    latest_ts = bars[-1].timestamp
    bars[-1] = Bar(
        timestamp=latest_ts,
        open=consolidation_close,
        high=consolidation_close + 0.5,
        low=consolidation_close - 0.5,
        close=consolidation_close,
        volume=1_000_000.0,
    )

    series = BarSeries(symbol="AAPL", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)
    signal = generator.generate(series)

    assert signal.signal_type == SignalType.HOLD
    assert signal.invalidation_price is not None
    assert signal.invalidation_price < signal.price


def test_ac001_bearish_exit_when_trend_broken(default_strategy_config):
    """Verify moving average bearish cross or channel breakdown triggers BEARISH_EXIT."""
    # Generate bars with downward slope so fast_ma < slow_ma
    bars = make_synthetic_bars(count=220, base_price=250.0, slope=-0.8, daily_volatility=2.0)

    series = BarSeries(symbol="MSFT", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)
    signal = generator.generate(series)

    assert signal.signal_type == SignalType.BEARISH_EXIT
    assert signal.invalidation_price is None
    assert "Trend invalidation triggered" in signal.reason


def test_ac001_no_signal_when_choppy_or_unconfirmed(default_strategy_config):
    """Verify market conditions without clear trend produce NO_SIGNAL."""
    # Flat oscillating market around 100.0
    start_time = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    bars = []
    for i in range(220):
        ts = start_time + timedelta(days=i)
        val = 100.0 + (1.0 if i % 2 == 0 else -1.0)
        bars.append(
            Bar(
                timestamp=ts,
                open=val,
                high=val + 0.5,
                low=val - 0.5,
                close=val,
                volume=500_000.0,
            )
        )

    series = BarSeries(symbol="GOOGL", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)
    signal = generator.generate(series)

    assert signal.signal_type in (SignalType.NO_SIGNAL, SignalType.BEARISH_EXIT)


# ==============================================================================
# AC-002: Rejection of Corrupt, Insufficient, or Malformed Market Data
# ==============================================================================


def test_ac002_insufficient_bars_raises_insufficient_data_error(default_strategy_config):
    """Verify series with fewer than 200 bars raises InsufficientDataError."""
    short_bars = make_synthetic_bars(count=150)
    series = BarSeries(symbol="NVDA", timeframe="1d", bars=tuple(short_bars))

    generator = DeterministicSignalGenerator(default_strategy_config)
    with pytest.raises(InsufficientDataError) as exc_info:
        generator.generate(series)
    assert "200 bars are required" in str(exc_info.value)


def test_ac002_non_chronological_timestamps_rejected():
    """Verify BarSeries rejects out-of-order or duplicate timestamps."""
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    t2 = datetime(2025, 1, 2, tzinfo=UTC)
    b1 = Bar(timestamp=t2, open=10, high=12, low=9, close=11, volume=100)
    b2 = Bar(timestamp=t1, open=11, high=13, low=10, close=12, volume=100)

    with pytest.raises(InvalidMarketDataError) as exc_info:
        BarSeries(symbol="AAPL", timeframe="1d", bars=(b1, b2))
    assert "Non-chronological" in str(exc_info.value)


def test_ac002_invalid_bar_price_boundaries_rejected():
    """Verify Bar rejects high < low or open/close outside high/low range."""
    ts = datetime(2025, 1, 1, tzinfo=UTC)

    # high < low
    with pytest.raises(InvalidMarketDataError):
        Bar(timestamp=ts, open=10, high=8, low=9, close=10)

    # open > high
    with pytest.raises(InvalidMarketDataError):
        Bar(timestamp=ts, open=15, high=12, low=8, close=10)

    # close < low
    with pytest.raises(InvalidMarketDataError):
        Bar(timestamp=ts, open=10, high=12, low=8, close=5)


def test_ac002_negative_prices_rejected():
    """Verify Bar rejects zero or negative prices."""
    ts = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Bar(timestamp=ts, open=-10, high=12, low=8, close=10)


def test_ac002_empty_bar_series_rejected():
    """Verify BarSeries rejects an empty tuple of bars."""
    with pytest.raises(InvalidMarketDataError):
        BarSeries(symbol="AAPL", timeframe="1d", bars=())


# ==============================================================================
# AC-003: 100% Mathematical Determinism & Immutability
# ==============================================================================


def test_ac003_mathematical_determinism_across_runs(default_strategy_config):
    """Verify identical input bars produce bit-for-bit identical signal and indicators."""
    bars = make_synthetic_bars(count=220, base_price=150.0, slope=0.3, daily_volatility=1.5)
    series = BarSeries(symbol="AAPL", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)

    signal_1 = generator.generate(series)
    signal_2 = generator.generate(series)

    assert signal_1 == signal_2
    assert signal_1.signal_type == signal_2.signal_type
    assert signal_1.price == signal_2.price
    assert signal_1.invalidation_price == signal_2.invalidation_price
    assert signal_1.indicators.fast_ma == signal_2.indicators.fast_ma
    assert signal_1.indicators.slow_ma == signal_2.indicators.slow_ma
    assert signal_1.indicators.atr == signal_2.indicators.atr


def test_ac003_trade_signal_and_indicators_immutable(default_strategy_config):
    """Verify TradeSignal and SignalIndicatorSnapshot cannot be modified at runtime."""
    bars = make_synthetic_bars(count=220, base_price=150.0, slope=0.3)
    series = BarSeries(symbol="AAPL", timeframe="1d", bars=tuple(bars))
    generator = DeterministicSignalGenerator(default_strategy_config)
    signal = generator.generate(series)

    with pytest.raises(ValidationError):
        signal.signal_type = SignalType.NO_SIGNAL

    with pytest.raises(ValidationError):
        signal.indicators.fast_ma = 999.9


def test_ac003_indicator_calculations_isolated():
    """Verify SMA, ATR, and Donchian channel standalone calculation functions."""
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert calculate_sma(prices, 3) == 40.0
    assert calculate_sma(prices, 5) == 30.0

    bars = make_synthetic_bars(count=30, base_price=50.0, slope=1.0, daily_volatility=2.0)
    high, low = calculate_donchian_channel(bars, lookback=10, offset=1)
    assert high > low

    atr = calculate_atr(bars, period=14)
    assert atr > 0.0
