"""TOSO Live Readiness Subsystem.

Provides pre-flight verification, open question resolution auditing,
safety checks, and controlled live deployment gate management.
"""

from toso.live.arming import (
    LiveArmingGate,
    compute_approval_token_signature,
    create_human_approval_token,
)
from toso.live.errors import (
    BrokerConnectivityError,
    CircuitBreakerCheckError,
    ClockSynchronizationError,
    ExpiredArmingSessionError,
    IncompleteAcceptanceMatrixError,
    InvalidApprovalTokenError,
    InvalidLiveParameterError,
    JournalIntegrityCheckError,
    LiveArmingError,
    LiveReadinessError,
    MissingTraceabilityError,
    PreFlightCheckFailedError,
    ReconciliationCheckError,
    UnauthorizedLiveDispatchError,
    UnresolvedLiveQuestionError,
)
from toso.live.matrix import AcceptanceMatrixAuditor
from toso.live.models import (
    AcceptanceMatrixAuditReceipt,
    CheckStatus,
    HumanApprovalToken,
    LiveArmingReceipt,
    LiveDeploymentMode,
    LiveReadinessAuditReceipt,
    PreFlightCheckItem,
    PreFlightCheckReceipt,
    QuestionResolution,
    QuestionStatus,
    RequirementCategory,
    RequirementTrace,
)
from toso.live.preflight import PreFlightSafetyEngine
from toso.live.resolution import LiveReadinessResolver

__all__ = [
    "AcceptanceMatrixAuditReceipt",
    "AcceptanceMatrixAuditor",
    "BrokerConnectivityError",
    "CheckStatus",
    "CircuitBreakerCheckError",
    "ClockSynchronizationError",
    "ExpiredArmingSessionError",
    "HumanApprovalToken",
    "IncompleteAcceptanceMatrixError",
    "InvalidApprovalTokenError",
    "InvalidLiveParameterError",
    "JournalIntegrityCheckError",
    "LiveArmingError",
    "LiveArmingGate",
    "LiveArmingReceipt",
    "LiveDeploymentMode",
    "LiveReadinessAuditReceipt",
    "LiveReadinessError",
    "LiveReadinessResolver",
    "MissingTraceabilityError",
    "PreFlightCheckFailedError",
    "PreFlightCheckItem",
    "PreFlightCheckReceipt",
    "PreFlightSafetyEngine",
    "QuestionResolution",
    "QuestionStatus",
    "ReconciliationCheckError",
    "RequirementCategory",
    "RequirementTrace",
    "UnauthorizedLiveDispatchError",
    "UnresolvedLiveQuestionError",
    "compute_approval_token_signature",
    "create_human_approval_token",
]
