"""TOSO Winner-Only Pyramiding and Aggregate Risk Errors.

Defines the error hierarchy for winner eligibility evaluation,
aggregate position risk boundary enforcement, and pyramid intake gating.
"""

from toso.entry.errors import InvalidationLooseningError
from toso.exit.errors import InvalidPositionStateError
from toso.sizing.errors import InsufficientCapitalError, PortfolioRiskLimitExceededError


class PyramidingError(Exception):
    """Base exception for all pyramiding-specific errors."""

    pass


class PyramidNotEligibleError(PyramidingError):
    """Raised when a position does not meet deterministic winner criteria to pyramid."""

    pass


class MaxPyramidAddsExceededError(PyramidingError):
    """Raised when an attempt is made to exceed max_pyramid_adds for a single position."""

    pass


class AggregateRiskLimitExceededError(PyramidingError):
    """Raised when resulting aggregate position risk exceeds max_aggregate_position_risk_pct."""

    pass


class PyramidConcentrationExceededError(PyramidingError):
    """Raised when a proposed pyramid would breach concentration limits.

    Blocks additional exposure without forcing liquidation of the existing winning position.
    """

    pass


__all__ = [
    "AggregateRiskLimitExceededError",
    "InsufficientCapitalError",
    "InvalidPositionStateError",
    "InvalidationLooseningError",
    "MaxPyramidAddsExceededError",
    "PortfolioRiskLimitExceededError",
    "PyramidConcentrationExceededError",
    "PyramidNotEligibleError",
    "PyramidingError",
]
