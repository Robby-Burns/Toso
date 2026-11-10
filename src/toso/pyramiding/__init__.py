"""TOSO Winner-Only Pyramiding Package.

Deterministic evaluation of winner qualification, aggregate risk boundaries,
concentration gating without forced liquidation, and pyramid order lifecycle.
"""

from toso.pyramiding.errors import (
    AggregateRiskLimitExceededError,
    InsufficientCapitalError,
    MaxPyramidAddsExceededError,
    PortfolioRiskLimitExceededError,
    PyramidConcentrationExceededError,
    PyramidingError,
    PyramidNotEligibleError,
)
from toso.pyramiding.evaluator import (
    apply_pyramid_to_position,
    calculate_aggregate_risk,
    create_pyramid_order,
    evaluate_pyramid_eligibility,
)
from toso.pyramiding.models import (
    AggregateRiskAssessment,
    PyramidEligibility,
    PyramidOrder,
)

__all__ = [
    "AggregateRiskAssessment",
    "AggregateRiskLimitExceededError",
    "InsufficientCapitalError",
    "MaxPyramidAddsExceededError",
    "PortfolioRiskLimitExceededError",
    "PyramidConcentrationExceededError",
    "PyramidEligibility",
    "PyramidNotEligibleError",
    "PyramidOrder",
    "PyramidingError",
    "apply_pyramid_to_position",
    "calculate_aggregate_risk",
    "create_pyramid_order",
    "evaluate_pyramid_eligibility",
]
