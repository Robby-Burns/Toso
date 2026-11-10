"""TOSO Live Readiness Error Taxonomy.

Defines deterministic, machine-readable exceptions for live pre-flight checks,
parameter resolution, open question tracking, and arming gate verification.
"""


class LiveReadinessError(Exception):
    """Base exception for all live readiness errors."""


class UnresolvedLiveQuestionError(LiveReadinessError):
    """Raised when an open question required for live execution is unresolved or invalid."""


class InvalidLiveParameterError(LiveReadinessError):
    """Raised when a resolved live parameter violates safety boundaries or invariants."""


class LiveArmingError(LiveReadinessError):
    """Raised when live system arming is rejected due to safety violations."""


class UnauthorizedLiveDispatchError(LiveArmingError):
    """Raised when live order dispatch is attempted while system is unarmed or unauthorized."""


class InvalidApprovalTokenError(LiveArmingError):
    """Raised when human approval token fails cryptographic signature or digest validation."""


class ExpiredArmingSessionError(LiveArmingError):
    """Raised when a live arming session exceeds maximum allowed duration window."""


class IncompleteAcceptanceMatrixError(LiveReadinessError):
    """Raised when the acceptance matrix contains unverified requirements."""


class MissingTraceabilityError(LiveReadinessError):
    """Raised when a requirement or invariant lacks verified test coverage."""


class PreFlightCheckFailedError(LiveReadinessError):
    """Raised when one or more pre-flight readiness checks fail."""


class ClockSynchronizationError(PreFlightCheckFailedError):
    """Raised when local clock skew against reference market time exceeds tolerance."""


class BrokerConnectivityError(PreFlightCheckFailedError):
    """Raised when broker connection, authentication, or session validation fails."""


class ReconciliationCheckError(PreFlightCheckFailedError):
    """Raised when broker state and local ledger fail clean-slate reconciliation."""


class CircuitBreakerCheckError(PreFlightCheckFailedError):
    """Raised when safety circuit breakers or kill-switch controls are unready."""


class JournalIntegrityCheckError(PreFlightCheckFailedError):
    """Raised when trade journal cryptographic hash continuity is compromised."""
