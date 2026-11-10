"""TOSO Paper Trading Error Taxonomy.

Defines deterministic, machine-readable exceptions for paper simulation,
order processing, margin/cash constraints, and fail-closed edge conditions.
"""


class PaperTradingError(Exception):
    """Base exception for all paper trading subsystem errors."""


class PaperOrderError(PaperTradingError):
    """Base exception for paper order processing and lifecycle errors."""


class InsufficientPaperFundsError(PaperOrderError):
    """Raised when an order exceeds available paper cash or buying power."""


class InvalidPaperOrderError(PaperOrderError):
    """Raised when an order intent contains invalid or unparseable parameters."""


class PaperOrderNotFoundError(PaperOrderError):
    """Raised when looking up or modifying a nonexistent paper order."""


class DuplicatePaperOrderError(PaperOrderError):
    """Raised when an order ID is duplicated or re-submitted inappropriately."""


class InvalidPaperStateError(PaperTradingError):
    """Raised on illegal paper broker state transitions or corrupt accounting."""


class UnknownPaperInputError(PaperTradingError):
    """Raised when corrupt, NaN, infinite, or unknown inputs are encountered (INV-008)."""


class EmergencyKillSwitchActiveError(PaperTradingError):
    """Raised when an order submission or operation is attempted while kill-switch is active."""
