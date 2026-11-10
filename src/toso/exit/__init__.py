"""TOSO Invalidation Exit Enforcement Module.

Exports domain models, evaluator services, and error classes
for non-discretionary exit enforcement and anti-averaging-down invariants.
"""

from toso.exit.errors import (
    AveragingDownProhibitedError,
    ExitError,
    InvalidationExitOverrideError,
    InvalidPositionStateError,
)
from toso.exit.evaluator import (
    evaluate_position_exit,
    execute_invalidation_exit,
    reject_agent_exit_delay,
    verify_no_averaging_down,
)
from toso.exit.models import (
    CoreExitOrder,
    CorePosition,
    ExitReason,
    PositionStatus,
)

__all__ = [
    "AveragingDownProhibitedError",
    "CoreExitOrder",
    "CorePosition",
    "ExitError",
    "ExitReason",
    "InvalidPositionStateError",
    "InvalidationExitOverrideError",
    "PositionStatus",
    "evaluate_position_exit",
    "execute_invalidation_exit",
    "reject_agent_exit_delay",
    "verify_no_averaging_down",
]
