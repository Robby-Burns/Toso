"""TOSO Market Data and Signal Models.

Provides strictly validated, immutable data structures for OHLCV bars,
time-series collections, indicator snapshots, and deterministic trade signals.
"""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from toso.signals.errors import InvalidMarketDataError


class SignalType(StrEnum):
    """Deterministic signal classification."""

    BULLISH_ENTRY = "BULLISH_ENTRY"
    BEARISH_EXIT = "BEARISH_EXIT"
    HOLD = "HOLD"
    NO_SIGNAL = "NO_SIGNAL"


class Bar(BaseModel):
    """Single OHLCV candlestick bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(..., description="Bar close/end timestamp (UTC).")
    open: float = Field(..., gt=0.0, description="Opening price.")
    high: float = Field(..., gt=0.0, description="Highest traded price.")
    low: float = Field(..., gt=0.0, description="Lowest traded price.")
    close: float = Field(..., gt=0.0, description="Closing price.")
    volume: float = Field(default=0.0, ge=0.0, description="Traded volume.")

    @model_validator(mode="after")
    def validate_price_boundaries(self) -> Self:
        """Ensure high is the extreme high and low is the extreme low."""
        if self.high < self.low:
            raise InvalidMarketDataError(
                f"Bar high ({self.high}) cannot be less than low ({self.low})."
            )
        if self.open > self.high or self.open < self.low:
            raise InvalidMarketDataError(
                f"Bar open ({self.open}) outside [low={self.low}, high={self.high}]."
            )
        if self.close > self.high or self.close < self.low:
            raise InvalidMarketDataError(
                f"Bar close ({self.close}) outside [low={self.low}, high={self.high}]."
            )
        return self


class BarSeries(BaseModel):
    """Chronologically sorted sequence of OHLCV bars for a specific instrument."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol, e.g. 'AAPL'.")
    timeframe: str = Field(default="1d", min_length=1, description="Bar frequency.")
    bars: tuple[Bar, ...] = Field(..., description="Tuple of strictly ordered bars.")

    @model_validator(mode="after")
    def validate_chronological_order(self) -> Self:
        """Verify that bars are non-empty and strictly increasing by timestamp."""
        if not self.bars:
            raise InvalidMarketDataError("BarSeries must contain at least one bar.")
        for i in range(1, len(self.bars)):
            prev_ts = self.bars[i - 1].timestamp
            curr_ts = self.bars[i].timestamp
            if curr_ts <= prev_ts:
                raise InvalidMarketDataError(
                    f"Non-chronological or duplicate timestamps at index {i}: "
                    f"{prev_ts.isoformat()} >= {curr_ts.isoformat()}."
                )
        return self

    def __len__(self) -> int:
        return len(self.bars)

    def __getitem__(self, index: int) -> Bar:
        return self.bars[index]

    @property
    def latest_bar(self) -> Bar:
        """Return the most recent bar in the series."""
        return self.bars[-1]

    def closes(self) -> list[float]:
        """Return list of close prices."""
        return [bar.close for bar in self.bars]

    def highs(self) -> list[float]:
        """Return list of high prices."""
        return [bar.high for bar in self.bars]

    def lows(self) -> list[float]:
        """Return list of low prices."""
        return [bar.low for bar in self.bars]


class SignalIndicatorSnapshot(BaseModel):
    """Snapshot of technical indicator values at the evaluation moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fast_ma: float = Field(..., description="Fast simple moving average.")
    slow_ma: float = Field(..., description="Slow simple moving average.")
    atr: float = Field(..., gt=0.0, description="Average True Range.")
    donchian_high: float = Field(..., description="Donchian breakout upper channel.")
    donchian_low: float = Field(..., description="Donchian breakout lower channel.")
    current_close: float = Field(..., description="Current bar close price.")


class TradeSignal(BaseModel):
    """Immutable, deterministic signal emitted by Core Strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., description="Instrument symbol.")
    timestamp: datetime = Field(..., description="Signal evaluation timestamp.")
    signal_type: SignalType = Field(..., description="Actionable signal classification.")
    price: float = Field(..., gt=0.0, description="Execution reference price (latest close).")
    invalidation_price: float | None = Field(
        default=None,
        description="Deterministic invalidation stop price (mandatory for BULLISH_ENTRY).",
    )
    indicators: SignalIndicatorSnapshot = Field(
        ..., description="Full mathematical indicator snapshot."
    )
    reason: str = Field(..., description="Deterministic explanation of the signal rule trigger.")
