"""TOSO Broker Order Reconciliation & Ambiguity Engine.

Implements FR-013, INV-009, INV-010, INV-008, INV-001, INV-006:
- Broker state governs actual order execution and fill truth (INV-009).
- Strict No-Blind-Retry: Ambiguous order outcomes must be reconciled before any
  conflicting action or retry can be submitted (INV-010).
- Pure deterministic evaluation and immutable receipts without wall-clock reads.
"""

import math
from datetime import datetime

from toso.broker.errors import (
    AmbiguousOrderOutcomeError,
    OrderAlreadyExecutedError,
    OrderDiscrepancyUnresolvableError,
    OrderReconciliationError,
    UnknownBrokerOrderInputError,
)
from toso.broker.models import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    LocalOrderSnapshot,
    OrderReconciliationReceipt,
    OrderReconciliationStatus,
)


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownBrokerOrderInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownBrokerOrderInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownBrokerOrderInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownBrokerOrderInputError(f"Field '{name}' cannot be negative, got {val}")


def reconcile_order_state(
    local_order: LocalOrderSnapshot,
    broker_order: BrokerOrderSnapshot | None,
    reconciliation_timestamp: datetime,
    in_flight_timeout_seconds: float = 30.0,
    receipt_id: str | None = None,
) -> OrderReconciliationReceipt:
    """Deterministically reconcile local order state with authoritative broker records.

    Enforces INV-009 (broker state governs order truth) and INV-010 (no blind retry
    while order outcome is ambiguous).

    Args:
        local_order: Locally tracked order state.
        broker_order: Authoritative order state from broker, or None if not found/timed out.
        reconciliation_timestamp: Pure clock anchor for evaluation.
        in_flight_timeout_seconds: Maximum grace window for in-flight orders without confirmation.
        receipt_id: Optional explicit receipt identifier.

    Returns:
        Immutable OrderReconciliationReceipt detailing authoritative status and retry eligibility.

    Raises:
        UnknownBrokerOrderInputError: If inputs are corrupted, NaN, or invalid.
    """
    if not isinstance(reconciliation_timestamp, datetime):
        raise UnknownBrokerOrderInputError(
            f"reconciliation_timestamp must be a datetime, got {reconciliation_timestamp}"
        )
    _validate_float(in_flight_timeout_seconds, "in_flight_timeout_seconds", must_be_positive=True)

    diagnostic_reasons: list[str] = []

    # Case 1: Broker has no record of the order (dropped connection, timeout, or in-flight lag)
    if broker_order is None:
        elapsed_seconds = (reconciliation_timestamp - local_order.timestamp).total_seconds()
        is_ambiguous = True
        can_retry = False
        status = OrderReconciliationStatus.AMBIGUOUS_OUTCOME
        authoritative_status = BrokerOrderStatus.AMBIGUOUS
        authoritative_filled_qty = 0
        authoritative_fill_price = None

        if elapsed_seconds < in_flight_timeout_seconds:
            diagnostic_reasons.append(
                f"Order {local_order.order_id} unconfirmed on broker within in-flight window "
                f"({elapsed_seconds:.1f}s < {in_flight_timeout_seconds:.1f}s). State is AMBIGUOUS."
            )
        else:
            diagnostic_reasons.append(
                f"Order {local_order.order_id} missing from broker response after timeout "
                f"({elapsed_seconds:.1f}s >= {in_flight_timeout_seconds:.1f}s)."
            )

    # Case 2: Broker returned an order record
    else:
        # Validate broker numeric fields
        _validate_float(broker_order.quantity, "broker_order.quantity", must_be_positive=True)
        if broker_order.average_fill_price is not None:
            _validate_float(
                broker_order.average_fill_price,
                "broker_order.average_fill_price",
                must_be_positive=True,
            )

        # Check fundamental identity consistency
        if (
            broker_order.order_id != local_order.order_id
            or broker_order.symbol != local_order.symbol
            or broker_order.side != local_order.side
            or broker_order.order_type != local_order.order_type
        ):
            status = OrderReconciliationStatus.DISCREPANCY_UNRESOLVABLE
            authoritative_status = BrokerOrderStatus.AMBIGUOUS
            authoritative_filled_qty = broker_order.filled_quantity
            authoritative_fill_price = broker_order.average_fill_price
            is_ambiguous = True
            can_retry = False
            diagnostic_reasons.append(
                f"Irreconcilable conflict for order {local_order.order_id}: local "
                f"({local_order.symbol} {local_order.side} {local_order.order_type}) vs broker "
                f"({broker_order.symbol} {broker_order.side} {broker_order.order_type})."
            )

        # Check explicit ambiguous status from broker
        elif broker_order.status == BrokerOrderStatus.AMBIGUOUS:
            status = OrderReconciliationStatus.AMBIGUOUS_OUTCOME
            authoritative_status = BrokerOrderStatus.AMBIGUOUS
            authoritative_filled_qty = broker_order.filled_quantity
            authoritative_fill_price = broker_order.average_fill_price
            is_ambiguous = True
            can_retry = False
            diagnostic_reasons.append(
                f"Broker explicitly reported order {local_order.order_id} as AMBIGUOUS."
            )

        # Valid broker order response: Broker state strictly governs (INV-009)
        else:
            authoritative_status = broker_order.status
            authoritative_filled_qty = broker_order.filled_quantity
            authoritative_fill_price = broker_order.average_fill_price
            is_ambiguous = False

            # Determine retry eligibility (INV-010):
            # Only terminal non-filled states (REJECTED, CANCELED, EXPIRED) can be retried
            if authoritative_status in (
                BrokerOrderStatus.REJECTED,
                BrokerOrderStatus.CANCELED,
                BrokerOrderStatus.EXPIRED,
            ):
                can_retry = True
            else:
                # Active or executed states: CANNOT RETRY (prevents duplicate execution)
                can_retry = False

            # Check if local state was already aligned
            if (
                local_order.status == broker_order.status
                and local_order.filled_quantity == broker_order.filled_quantity
            ):
                status = OrderReconciliationStatus.RECONCILED_MATCHED
                diagnostic_reasons.append(
                    f"Order {local_order.order_id} matched state ({authoritative_status.value})."
                )
            else:
                status = OrderReconciliationStatus.RECONCILED_BROKER_GOVERNED
                diagnostic_reasons.append(
                    f"Order {local_order.order_id} reconciled to broker truth: local "
                    f"'{local_order.status.value}' -> broker '{authoritative_status.value}' "
                    f"(filled {authoritative_filled_qty} shares)."
                )

    if receipt_id is None:
        receipt_id = f"RECON-ORD-{local_order.order_id}-{int(reconciliation_timestamp.timestamp())}"

    return OrderReconciliationReceipt(
        receipt_id=receipt_id,
        timestamp=reconciliation_timestamp,
        order_id=local_order.order_id,
        status=status,
        authoritative_status=authoritative_status,
        authoritative_filled_quantity=authoritative_filled_qty,
        authoritative_fill_price=authoritative_fill_price,
        is_ambiguous=is_ambiguous,
        can_retry=can_retry,
        diagnostic_reasons=tuple(diagnostic_reasons),
    )


def enforce_order_submission_safety(receipt: OrderReconciliationReceipt) -> None:
    """Enforce strict safety gating before any order submission or retry (INV-010).

    Raises:
        AmbiguousOrderOutcomeError: If order outcome is ambiguous (no blind retry).
        OrderAlreadyExecutedError: If order has already executed or is active.
        OrderDiscrepancyUnresolvableError: If order has irreconcilable conflicts.
        OrderReconciliationError: If order cannot be submitted/retried.
    """
    reasons_str = "; ".join(receipt.diagnostic_reasons) or receipt.status.value

    # Precedence 1: Irreconcilable conflict
    if receipt.status == OrderReconciliationStatus.DISCREPANCY_UNRESOLVABLE:
        raise OrderDiscrepancyUnresolvableError(
            f"Cannot retry or submit order {receipt.order_id}: Unresolvable conflict. "
            f"Details: {reasons_str}"
        )

    # Precedence 2: Ambiguity Blocks Everything (INV-010)
    if receipt.is_ambiguous:
        raise AmbiguousOrderOutcomeError(
            f"Cannot retry or submit order {receipt.order_id}: Outcome is ambiguous. "
            f"Reconciliation required before action (INV-010). Details: {reasons_str}"
        )

    # Precedence 3: Already Executed or In-Flight
    if receipt.authoritative_status in (
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.PARTIALLY_FILLED,
    ):
        raise OrderAlreadyExecutedError(
            f"Cannot resubmit order {receipt.order_id}: Already executed "
            f"({receipt.authoritative_status.value}, {receipt.authoritative_filled_quantity} qty). "
            f"Blind retry prohibited."
        )

    if receipt.authoritative_status in (
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.SUBMITTED,
        BrokerOrderStatus.PENDING_SUBMIT,
    ):
        raise OrderAlreadyExecutedError(
            f"Cannot resubmit order {receipt.order_id}: Order is active on broker "
            f"({receipt.authoritative_status.value}). Duplicate submission prohibited."
        )

    # Precedence 4: General retry eligibility
    if not receipt.can_retry:
        raise OrderReconciliationError(
            f"Cannot retry order {receipt.order_id}: Not eligible for retry ({reasons_str})."
        )


def resolve_ambiguous_order(
    ambiguous_receipt: OrderReconciliationReceipt,
    confirmed_broker_order: BrokerOrderSnapshot,
    resolution_timestamp: datetime,
) -> OrderReconciliationReceipt:
    """Deterministically resolve an ambiguous order using an authoritative broker query (AC-001).

    Converts an ambiguous outcome into a confirmed, broker-governed state once authoritative
    records are returned from broker inquiry.
    """
    local_order = LocalOrderSnapshot(
        order_id=ambiguous_receipt.order_id,
        symbol=confirmed_broker_order.symbol,
        side=confirmed_broker_order.side,
        order_type=confirmed_broker_order.order_type,
        quantity=confirmed_broker_order.quantity,
        filled_quantity=ambiguous_receipt.authoritative_filled_quantity,
        status=ambiguous_receipt.authoritative_status,
        timestamp=ambiguous_receipt.timestamp,
    )
    return reconcile_order_state(
        local_order=local_order,
        broker_order=confirmed_broker_order,
        reconciliation_timestamp=resolution_timestamp,
    )
