"""TOSO Broker Integrity Subsystem Errors.

Defines error taxonomy for broker authority, account, order, and position reconciliation,
and ambiguous outcome handling (FR-013, INV-009, INV-010).
"""


class BrokerIntegrityError(Exception):
    """Base exception for all Broker Integrity subsystem errors."""


class AccountReconciliationError(BrokerIntegrityError):
    """Base exception for all account-level reconciliation failures."""


class MaterialAccountDiscrepancyError(AccountReconciliationError):
    """Raised when local account state differs materially from authoritative broker state."""


class StaleBrokerAccountDataError(AccountReconciliationError):
    """Raised when broker account snapshot exceeds allowable freshness threshold."""


class UnknownBrokerAccountInputError(AccountReconciliationError):
    """Raised when broker account input data is missing, NaN, infinite, or corrupted (INV-008)."""


class BrokerDisconnectedError(AccountReconciliationError):
    """Raised when broker connection is offline or unavailable during reconciliation."""


class OrderReconciliationError(BrokerIntegrityError):
    """Base exception for all order-level reconciliation failures."""


class AmbiguousOrderOutcomeError(OrderReconciliationError):
    """Raised when attempting an action while order outcome is ambiguous (INV-010)."""


class UnresolvedAmbiguityError(AmbiguousOrderOutcomeError):
    """Raised when attempting an action while an ambiguous order remains unresolved (INV-010)."""


class ConflictingActionBlockedError(BrokerIntegrityError):
    """Raised when a new action is attempted on a symbol with unresolved ambiguity (INV-010)."""


class OrderAlreadyExecutedError(OrderReconciliationError):
    """Raised when attempting to resubmit or duplicate an already filled or active order."""


class OrderDiscrepancyUnresolvableError(OrderReconciliationError):
    """Raised when local and broker order states have irreconcilable fundamental conflicts."""


class UnknownBrokerOrderInputError(OrderReconciliationError):
    """Raised when order input data is missing, non-positive, NaN, or corrupted (INV-008)."""


class PositionReconciliationError(BrokerIntegrityError):
    """Base exception for all position-level reconciliation failures."""


class MaterialPositionDiscrepancyError(PositionReconciliationError):
    """Raised when local position state differs materially from authoritative broker state."""


class PhantomPositionDetectedError(PositionReconciliationError):
    """Raised when an unexpected position exists on the broker that is missing locally."""


class GhostPositionDetectedError(PositionReconciliationError):
    """Raised when a locally recorded position is confirmed closed/missing on the broker."""


class UnknownBrokerPositionInputError(PositionReconciliationError):
    """Raised when position input data is missing, non-positive, NaN, or corrupted (INV-008)."""


class IdempotencyError(BrokerIntegrityError):
    """Base exception for order submission idempotency failures (Section 11.6)."""


class DuplicateOrderSubmissionError(IdempotencyError):
    """Raised when a duplicate order submission is intercepted (Section 11.6)."""


class ConflictingOrderIdentityError(IdempotencyError):
    """Raised when an existing order ID is reused with conflicting parameters (INV-008)."""


class ConflictingOrderInFlightError(IdempotencyError):
    """Raised when a new order is submitted for a symbol with an in-flight order (INV-010)."""
