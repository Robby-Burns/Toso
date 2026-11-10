"""TOSO Position Sizing and Capital Allocation Errors.

Defines the error hierarchy for deterministic position sizing,
gap/slippage calculations, and capital concentration constraints.
"""


class SizingError(Exception):
    """Base exception for all position sizing and capital allocation errors."""

    pass


class InsufficientCapitalError(SizingError):
    """Raised when available capital cannot purchase at least 1 share under risk rules."""

    pass


class ConcentrationLimitExceededError(SizingError):
    """Raised when position exposure or intended market value exceeds concentration limits."""

    pass


class PortfolioRiskLimitExceededError(SizingError):
    """Raised when open portfolio risk reaches or exceeds the configured maximum portfolio risk."""

    pass


class InvalidSizingInputError(SizingError):
    """Raised when input parameters to sizing calculator are invalid (non-positive equity, etc.)."""

    pass
