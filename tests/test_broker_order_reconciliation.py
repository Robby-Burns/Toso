"""Verification Test Suite for Story BROKER-002: Order Reconciliation & Ambiguity Handling.

Verifies:
- FR-013: Broker state is authoritative for order execution, fills, and status.
- INV-009: When local state conflicts with broker state, broker state governs.
- INV-010: No blind retry: Ambiguous order outcomes must be reconciled before action.
- INV-008: Fail closed on corrupt, NaN, missing, or contradictory order data.
- INV-001 & INV-006: Pure determinism and immutable audit receipts.
"""

from datetime import UTC, datetime

import pytest

from toso.broker.errors import (
    AmbiguousOrderOutcomeError,
    OrderAlreadyExecutedError,
    OrderDiscrepancyUnresolvableError,
    UnknownBrokerOrderInputError,
)
from toso.broker.models import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    LocalOrderSnapshot,
    OrderReconciliationStatus,
    OrderSide,
    OrderType,
)
from toso.broker.orders import (
    _validate_float,
    enforce_order_submission_safety,
    reconcile_order_state,
    resolve_ambiguous_order,
)


@pytest.fixture
def eval_timestamp() -> datetime:
    return datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_local_order(eval_timestamp: datetime) -> LocalOrderSnapshot:
    return LocalOrderSnapshot(
        order_id="ORD-AAPL-001",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=0,
        status=BrokerOrderStatus.SUBMITTED,
        timestamp=eval_timestamp,
    )


@pytest.fixture
def sample_broker_order(eval_timestamp: datetime) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        order_id="ORD-AAPL-001",
        broker_order_id="BRK-99901",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=100,
        average_fill_price=150.25,
        status=BrokerOrderStatus.FILLED,
        timestamp=eval_timestamp,
        is_terminal=True,
    )


def test_ac001_reconcile_ambiguous_outcome_after_timeout(
    sample_local_order: LocalOrderSnapshot,
    sample_broker_order: BrokerOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-001 / INV-010: Timeout produces ambiguous receipt; subsequent query resolves."""
    # 1. Simulating broker timeout: broker returns None
    ambiguous_receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=None,
        reconciliation_timestamp=eval_timestamp,
        in_flight_timeout_seconds=30.0,
    )

    assert ambiguous_receipt.status == OrderReconciliationStatus.AMBIGUOUS_OUTCOME
    assert ambiguous_receipt.is_ambiguous is True
    assert ambiguous_receipt.can_retry is False
    assert ambiguous_receipt.authoritative_status == BrokerOrderStatus.AMBIGUOUS

    # 2. Safety check blocks any retry while ambiguous (INV-010)
    with pytest.raises(AmbiguousOrderOutcomeError):
        enforce_order_submission_safety(ambiguous_receipt)

    # 3. Subsequent query receives confirmed broker order
    resolution_ts = datetime(2026, 10, 28, 12, 0, 15, tzinfo=UTC)
    resolved_receipt = resolve_ambiguous_order(
        ambiguous_receipt=ambiguous_receipt,
        confirmed_broker_order=sample_broker_order,
        resolution_timestamp=resolution_ts,
    )

    assert resolved_receipt.status == OrderReconciliationStatus.RECONCILED_BROKER_GOVERNED
    assert resolved_receipt.is_ambiguous is False
    assert resolved_receipt.authoritative_status == BrokerOrderStatus.FILLED
    assert resolved_receipt.authoritative_filled_quantity == 100
    assert resolved_receipt.authoritative_fill_price == 150.25


def test_ac002_no_blind_retry_on_ambiguous_order(
    sample_local_order: LocalOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-002 / INV-010: System strictly forbids blind retry on ambiguous orders."""
    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=None,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.is_ambiguous is True
    assert receipt.can_retry is False

    with pytest.raises(AmbiguousOrderOutcomeError) as exc_info:
        enforce_order_submission_safety(receipt)
    assert "Outcome is ambiguous" in str(exc_info.value)
    assert "INV-010" in str(exc_info.value)


def test_ac003_broker_state_governs_local_state(
    sample_local_order: LocalOrderSnapshot,
    sample_broker_order: BrokerOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-003 & INV-009: When local differs from broker, broker state strictly governs."""
    # Local thinks SUBMITTED with 0 fills; broker confirms FILLED with 100 fills at $150.25
    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=sample_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == OrderReconciliationStatus.RECONCILED_BROKER_GOVERNED
    assert receipt.authoritative_status == BrokerOrderStatus.FILLED
    assert receipt.authoritative_filled_quantity == 100
    assert receipt.authoritative_fill_price == 150.25
    assert receipt.is_ambiguous is False


def test_duplicate_submission_blocked_on_filled_order(
    sample_local_order: LocalOrderSnapshot,
    sample_broker_order: BrokerOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify that attempting to resubmit or retry an already filled order is blocked."""
    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=sample_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.can_retry is False

    with pytest.raises(OrderAlreadyExecutedError) as exc_info:
        enforce_order_submission_safety(receipt)
    assert "Already executed" in str(exc_info.value)


def test_duplicate_submission_blocked_on_active_order(
    sample_local_order: LocalOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify that attempting to retry an order currently active on the broker is blocked."""
    active_broker_order = BrokerOrderSnapshot(
        order_id="ORD-AAPL-001",
        broker_order_id="BRK-99902",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=0,
        status=BrokerOrderStatus.ACCEPTED,
        timestamp=eval_timestamp,
        is_terminal=False,
    )

    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=active_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.can_retry is False
    with pytest.raises(OrderAlreadyExecutedError) as exc_info:
        enforce_order_submission_safety(receipt)
    assert "Order is active on broker" in str(exc_info.value)


def test_retry_permitted_on_rejected_or_canceled_order(
    sample_local_order: LocalOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify that orders confirmed rejected or canceled by broker are safe to retry."""
    rejected_broker_order = BrokerOrderSnapshot(
        order_id="ORD-AAPL-001",
        broker_order_id="BRK-99903",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=0,
        status=BrokerOrderStatus.REJECTED,
        timestamp=eval_timestamp,
        is_terminal=True,
    )

    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=rejected_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.authoritative_status == BrokerOrderStatus.REJECTED
    assert receipt.can_retry is True
    assert receipt.is_ambiguous is False

    # Enforce should succeed cleanly
    enforce_order_submission_safety(receipt)


def test_unresolvable_order_discrepancy(
    sample_local_order: LocalOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify that conflicting symbols/sides for same order ID fails with unresolvable error."""
    conflicting_broker_order = BrokerOrderSnapshot(
        order_id="ORD-AAPL-001",  # Same order ID
        broker_order_id="BRK-99904",
        symbol="MSFT",  # Differs from AAPL!
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=0,
        status=BrokerOrderStatus.ACCEPTED,
        timestamp=eval_timestamp,
    )

    receipt = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=conflicting_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == OrderReconciliationStatus.DISCREPANCY_UNRESOLVABLE
    assert receipt.can_retry is False

    with pytest.raises(OrderDiscrepancyUnresolvableError) as exc_info:
        enforce_order_submission_safety(receipt)
    assert "Unresolvable conflict" in str(exc_info.value)


def test_fail_closed_on_corrupt_unknown_inputs(
    sample_local_order: LocalOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify INV-008: Non-numeric, NaN, or corrupt inputs fail closed."""
    with pytest.raises(UnknownBrokerOrderInputError):
        _validate_float(float("nan"), "test_nan")

    with pytest.raises(UnknownBrokerOrderInputError):
        _validate_float(float("inf"), "test_inf")

    with pytest.raises(UnknownBrokerOrderInputError):
        _validate_float(-5.0, "test_negative", must_be_positive=True)

    with pytest.raises(UnknownBrokerOrderInputError):
        _validate_float("not-numeric", "test_str")  # type: ignore

    # Non-datetime timestamp
    with pytest.raises(UnknownBrokerOrderInputError):
        reconcile_order_state(
            local_order=sample_local_order,
            broker_order=None,
            reconciliation_timestamp="invalid-ts",  # type: ignore
        )


def test_pure_determinism_and_immutable_receipts(
    sample_local_order: LocalOrderSnapshot,
    sample_broker_order: BrokerOrderSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify INV-001 & INV-006: Identical inputs produce identical receipts; immutable models."""
    receipt1 = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=sample_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )
    receipt2 = reconcile_order_state(
        local_order=sample_local_order,
        broker_order=sample_broker_order,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt1 == receipt2
    assert receipt1.receipt_id == receipt2.receipt_id

    # Immutability check
    with pytest.raises(Exception):
        receipt1.status = OrderReconciliationStatus.FAILED_CLOSED  # type: ignore
