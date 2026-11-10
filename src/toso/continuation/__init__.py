"""TOSO Winner Continuation & Anti-Take-Profit Rule Package."""

from toso.continuation.errors import (
    ContinuationError,
    DiscretionaryExitProhibitedError,
    ForcedConcentrationExitProhibitedError,
    InvalidContinuationStateError,
    PrematureTrimmingProhibitedError,
    TakeProfitProhibitedError,
)
from toso.continuation.evaluator import (
    APPROVED_ORDER_FIELDS,
    evaluate_winner_continuation,
    prevent_target_price_registration,
    validate_exit_request,
    verify_winner_continuation_on_bar,
)
from toso.continuation.models import (
    ContinuationStatus,
    ExitType,
    ExitValidationReceipt,
    WinnerContinuationDecision,
)

__all__ = [
    "ContinuationError",
    "TakeProfitProhibitedError",
    "PrematureTrimmingProhibitedError",
    "ForcedConcentrationExitProhibitedError",
    "DiscretionaryExitProhibitedError",
    "InvalidContinuationStateError",
    "ContinuationStatus",
    "ExitType",
    "WinnerContinuationDecision",
    "ExitValidationReceipt",
    "APPROVED_ORDER_FIELDS",
    "evaluate_winner_continuation",
    "validate_exit_request",
    "prevent_target_price_registration",
    "verify_winner_continuation_on_bar",
]
