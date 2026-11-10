"""TOSO Signal Generation Errors.

Explicit error taxonomy for market data validation, indicator calculation,
and deterministic signal evaluation.
"""


class SignalError(Exception):
    """Base error for all signal-related failures."""


class InsufficientDataError(SignalError):
    """Raised when market data bars are fewer than required indicator lookback periods."""


class InvalidMarketDataError(SignalError):
    """Raised when bar data contains invalid prices, volumes, or non-chronological order."""


class NonDeterministicSignalError(SignalError):
    """Raised if an unapproved or ambiguous signal condition is detected."""
