"""TOSO Position Sizing and Capital Allocation Package.

Deterministic position sizing, gap/slippage risk modeling,
concentration gating, and portfolio risk boundary enforcement.
"""

from toso.sizing.calculator import calculate_position_size, verify_concentration_limit
from toso.sizing.errors import (
    ConcentrationLimitExceededError,
    InsufficientCapitalError,
    InvalidSizingInputError,
    PortfolioRiskLimitExceededError,
    SizingError,
)
from toso.sizing.models import (
    GapScenarioLoss,
    PositionSizingDecision,
    SizingConstraint,
)

__all__ = [
    "ConcentrationLimitExceededError",
    "GapScenarioLoss",
    "InsufficientCapitalError",
    "InvalidSizingInputError",
    "PortfolioRiskLimitExceededError",
    "PositionSizingDecision",
    "SizingConstraint",
    "SizingError",
    "calculate_position_size",
    "verify_concentration_limit",
]
