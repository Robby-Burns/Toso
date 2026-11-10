"""TOSO Signals Package.

Public interfaces for market data models, technical indicators, and deterministic signal generation.
"""

from toso.signals.errors import (
    InsufficientDataError,
    InvalidMarketDataError,
    NonDeterministicSignalError,
    SignalError,
)
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.indicators import (
    calculate_atr,
    calculate_donchian_channel,
    calculate_sma,
)
from toso.signals.models import (
    Bar,
    BarSeries,
    SignalIndicatorSnapshot,
    SignalType,
    TradeSignal,
)

__all__ = [
    "Bar",
    "BarSeries",
    "DeterministicSignalGenerator",
    "InsufficientDataError",
    "InvalidMarketDataError",
    "NonDeterministicSignalError",
    "SignalError",
    "SignalIndicatorSnapshot",
    "SignalType",
    "TradeSignal",
    "calculate_atr",
    "calculate_donchian_channel",
    "calculate_sma",
]
