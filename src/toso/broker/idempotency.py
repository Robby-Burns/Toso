"""TOSO Broker Idempotent Order Identity & Submission Engine.

Implements Section 11.6, FR-013, INV-010, INV-009, INV-008, INV-001, INV-006:
- Cryptographically deterministic client order ID generation (INV-001).
- Double-submission interceptor preventing duplicate trading effects (Section 11.6).
- Fail-closed gating on conflicting order payloads reusing existing order IDs (INV-008).
- In-flight conflict prevention blocking concurrent orders for the same symbol (INV-010).
- Pure evaluation and immutable audit receipts without wall-clock reads (INV-001, INV-006).
"""

import hashlib
import math
from collections.abc import Sequence
from datetime import datetime

from toso.broker.errors import (
    ConflictingOrderIdentityError,
    ConflictingOrderInFlightError,
    DuplicateOrderSubmissionError,
    UnknownBrokerOrderInputError,
)
from toso.broker.models import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    IdempotentSubmissionReceipt,
    OrderIntent,
    SubmissionDisposition,
)

ACTIVE_STATUSES = frozenset(
    {
        BrokerOrderStatus.PENDING_SUBMIT,
        BrokerOrderStatus.SUBMITTED,
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.AMBIGUOUS,
    }
)


def _validate_float_optional(val: float | None, name: str) -> None:
    """Validate optional price floats (INV-008)."""
    if val is not None:
        if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val) or val <= 0.0:
            raise UnknownBrokerOrderInputError(
                f"Field '{name}' must be a strictly positive finite float, got {val}"
            )


def generate_idempotent_order_id(intent: OrderIntent) -> str:
    """Generate a deterministic client order ID for an order intent (INV-001).

    Derived cryptographically from trade decision attributes:
    signal_id, symbol, side, order_type, quantity, price anchors, and intent timestamp.

    Args:
        intent: OrderIntent representing proposed order parameters.

    Returns:
        Deterministic alphanumeric client order ID (e.g. ORD-AAPL-A1B2C3D4E5F67890).
    """
    ts_sec = int(intent.intent_timestamp.timestamp())
    payload = (
        f"{intent.signal_id}:{intent.symbol}:{intent.side}:"
        f"{intent.order_type}:{intent.quantity}:{intent.limit_price}:"
        f"{intent.stop_price}:{ts_sec}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16].upper()
    return f"ORD-{intent.symbol}-{digest}"


def compute_idempotency_key(intent: OrderIntent) -> str:
    """Compute the deterministic cryptographic idempotency key for an intent."""
    ts_sec = int(intent.intent_timestamp.timestamp())
    payload = (
        f"{intent.signal_id}:{intent.symbol}:{intent.side}:"
        f"{intent.order_type}:{intent.quantity}:{intent.limit_price}:"
        f"{intent.stop_price}:{ts_sec}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IdempotentOrderRegistry:
    """In-memory deterministic registry tracking submitted order intents and lifecycles."""

    def __init__(self) -> None:
        self._intents_by_id: dict[str, OrderIntent] = {}
        self._receipts_by_id: dict[str, IdempotentSubmissionReceipt] = {}
        self._order_statuses: dict[str, BrokerOrderStatus] = {}
        self._symbol_active_orders: dict[str, str] = {}  # symbol -> active client_order_id

    def evaluate_and_register(
        self,
        intent: OrderIntent,
        evaluation_timestamp: datetime,
        active_broker_orders: Sequence[BrokerOrderSnapshot] = (),
        receipt_id: str | None = None,
    ) -> IdempotentSubmissionReceipt:
        """Evaluate order submission idempotency and register if safe.

        Args:
            intent: Proposed OrderIntent.
            evaluation_timestamp: Clock anchor echoed into audit receipt (INV-006).
            active_broker_orders: Current active orders reported by broker feed.
            receipt_id: Optional explicit receipt ID. Generated deterministically if None.

        Returns:
            IdempotentSubmissionReceipt documenting disposition.

        Raises:
            UnknownBrokerOrderInputError: On malformed or non-positive inputs (INV-008).
            ConflictingOrderIdentityError: When an ID is reused with conflicting parameters.
            ConflictingOrderInFlightError: When an order for the same symbol is already active.
        """
        # 1. Fail-closed input validations (INV-008)
        if not intent.symbol or not isinstance(intent.symbol, str):
            raise UnknownBrokerOrderInputError(f"Invalid intent symbol: {intent.symbol}")
        if not intent.signal_id or not isinstance(intent.signal_id, str):
            raise UnknownBrokerOrderInputError(f"Invalid signal_id: {intent.signal_id}")
        if not isinstance(intent.quantity, int) or intent.quantity <= 0:
            raise UnknownBrokerOrderInputError(f"Invalid quantity: {intent.quantity}")
        _validate_float_optional(intent.limit_price, "limit_price")
        _validate_float_optional(intent.stop_price, "stop_price")
        if evaluation_timestamp is None or not isinstance(evaluation_timestamp, datetime):
            raise UnknownBrokerOrderInputError(
                f"Invalid evaluation_timestamp: {evaluation_timestamp}"
            )

        # 2. Derive authoritative client_order_id & idempotency_key
        client_order_id = intent.client_order_id or generate_idempotent_order_id(intent)
        idempotency_key = compute_idempotency_key(intent)

        # 3. Check for existing registration with identical client_order_id
        if client_order_id in self._intents_by_id:
            existing = self._intents_by_id[client_order_id]
            # Check for parameter conflicts under the same ID
            if (
                existing.symbol != intent.symbol
                or existing.side != intent.side
                or existing.order_type != intent.order_type
                or existing.quantity != intent.quantity
                or existing.limit_price != intent.limit_price
                or existing.stop_price != intent.stop_price
            ):
                raise ConflictingOrderIdentityError(
                    f"Order ID {client_order_id} reused with conflicting parameters: "
                    f"existing ({existing.symbol} {existing.side} {existing.quantity}) vs "
                    f"proposed ({intent.symbol} {intent.side} {intent.quantity})."
                )

            # Identical parameters: Duplicate submission intercepted! (Section 11.6)
            receipt_id = (
                receipt_id or f"IDEMP-DUP-{client_order_id}-{int(evaluation_timestamp.timestamp())}"
            )
            msg = (
                f"Duplicate submission intercepted for order {client_order_id} "
                f"({intent.symbol}); double-execution prevented (Section 11.6)."
            )
            receipt = IdempotentSubmissionReceipt(
                receipt_id=receipt_id,
                timestamp=evaluation_timestamp,
                client_order_id=client_order_id,
                idempotency_key=idempotency_key,
                disposition=SubmissionDisposition.DUPLICATE_INTERCEPTED,
                is_safe_to_submit=False,
                order_intent=intent,
                diagnostic_reasons=(msg,),
            )
            return receipt

        # 4. Check for in-flight conflict on the same symbol (INV-010)
        # Check broker feed active orders
        for bo in active_broker_orders:
            if bo.symbol == intent.symbol and bo.status in ACTIVE_STATUSES:
                raise ConflictingOrderInFlightError(
                    f"Cannot submit order for {intent.symbol}: active broker order "
                    f"{bo.order_id} is in-flight ({bo.status})."
                )

        # Check local registry active orders for symbol
        if intent.symbol in self._symbol_active_orders:
            active_id = self._symbol_active_orders[intent.symbol]
            status = self._order_statuses.get(active_id, BrokerOrderStatus.PENDING_SUBMIT)
            if status in ACTIVE_STATUSES:
                raise ConflictingOrderInFlightError(
                    f"Cannot submit order for {intent.symbol}: order {active_id} "
                    f"is already in-flight locally ({status})."
                )

        # 5. Safe new submission
        receipt_id = (
            receipt_id or f"IDEMP-SUB-{client_order_id}-{int(evaluation_timestamp.timestamp())}"
        )
        msg = (
            f"Order {client_order_id} ({intent.symbol} {intent.side} {intent.quantity}) "
            "accepted for initial broker submission (SUBMITTED_NEW)."
        )
        receipt = IdempotentSubmissionReceipt(
            receipt_id=receipt_id,
            timestamp=evaluation_timestamp,
            client_order_id=client_order_id,
            idempotency_key=idempotency_key,
            disposition=SubmissionDisposition.SUBMITTED_NEW,
            is_safe_to_submit=True,
            order_intent=intent,
            diagnostic_reasons=(msg,),
        )

        # Register in tracking state
        self._intents_by_id[client_order_id] = intent
        self._receipts_by_id[client_order_id] = receipt
        self._order_statuses[client_order_id] = BrokerOrderStatus.PENDING_SUBMIT
        self._symbol_active_orders[intent.symbol] = client_order_id

        return receipt

    def update_order_status(self, client_order_id: str, status: BrokerOrderStatus) -> None:
        """Update tracked status of an order."""
        self._order_statuses[client_order_id] = status
        intent = self._intents_by_id.get(client_order_id)
        if intent and status not in ACTIVE_STATUSES:
            # Order reached terminal state; release symbol lock
            if self._symbol_active_orders.get(intent.symbol) == client_order_id:
                del self._symbol_active_orders[intent.symbol]

    def is_symbol_in_flight(self, symbol: str) -> bool:
        """Check whether an order is currently active/in-flight for a symbol."""
        active_id = self._symbol_active_orders.get(symbol)
        if not active_id:
            return False
        return (
            self._order_statuses.get(active_id, BrokerOrderStatus.PENDING_SUBMIT) in ACTIVE_STATUSES
        )


def evaluate_order_submission_idempotency(
    intent: OrderIntent,
    registry: IdempotentOrderRegistry,
    evaluation_timestamp: datetime,
    active_broker_orders: Sequence[BrokerOrderSnapshot] = (),
    receipt_id: str | None = None,
) -> IdempotentSubmissionReceipt:
    """Pure evaluation function delegating to IdempotentOrderRegistry."""
    return registry.evaluate_and_register(
        intent=intent,
        evaluation_timestamp=evaluation_timestamp,
        active_broker_orders=active_broker_orders,
        receipt_id=receipt_id,
    )


def enforce_submission_idempotency(receipt: IdempotentSubmissionReceipt) -> bool:
    """Enforce idempotency submission gating (Section 11.6).

    Args:
        receipt: IdempotentSubmissionReceipt.

    Returns:
        True if the order is safe to proceed to broker submission.

    Raises:
        DuplicateOrderSubmissionError: If the order was intercepted as a duplicate.
    """
    if receipt.disposition == SubmissionDisposition.DUPLICATE_INTERCEPTED:
        raise DuplicateOrderSubmissionError(receipt.diagnostic_reasons[0])

    return receipt.is_safe_to_submit
