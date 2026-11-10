"""Unit and integration tests for TOSO Idempotent Order Identity & Submission (BROKER-005).

Verifies Section 11.6, FR-013, INV-010, INV-009, INV-008, INV-001, INV-006:
- Pure determinism of idempotent client order identity generation (INV-001).
- Double-submission interceptor preventing duplicate trading effects (Section 11.6).
- Conflicting order payloads reusing existing order IDs fail closed (INV-008).
- Concurrent/in-flight conflicts on the same symbol are blocked (INV-010).
- Safe retry lifecycle on terminal order failure.
- Fail-closed behavior on corrupted, NaN, or non-positive inputs (INV-008).
- Immutability and auditability of submission receipts (INV-001, INV-006).
"""

from datetime import UTC, datetime

import pytest

from toso.broker.errors import (
    ConflictingOrderIdentityError,
    ConflictingOrderInFlightError,
    DuplicateOrderSubmissionError,
    UnknownBrokerOrderInputError,
)
from toso.broker.idempotency import (
    IdempotentOrderRegistry,
    compute_idempotency_key,
    enforce_submission_idempotency,
    evaluate_order_submission_idempotency,
    generate_idempotent_order_id,
)
from toso.broker.models import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    OrderIntent,
    OrderSide,
    OrderType,
    SubmissionDisposition,
)


@pytest.fixture
def base_timestamp() -> datetime:
    return datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_intent(base_timestamp: datetime) -> OrderIntent:
    return OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        limit_price=150.0,
        stop_price=142.5,
        signal_id="SIG-CORE-001",
        intent_timestamp=base_timestamp,
    )


def test_ac001_deterministic_order_identity(
    sample_intent: OrderIntent,
    base_timestamp: datetime,
) -> None:
    """AC-001: Identical order intents produce identical client order IDs (INV-001)."""
    id1 = generate_idempotent_order_id(sample_intent)
    id2 = generate_idempotent_order_id(sample_intent)
    key1 = compute_idempotency_key(sample_intent)
    key2 = compute_idempotency_key(sample_intent)

    assert id1 == id2
    assert key1 == key2
    assert id1.startswith("ORD-AAPL-")

    # Modifying parameters produces distinct identity
    different_intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=101,  # 1 share difference
        limit_price=150.0,
        stop_price=142.5,
        signal_id="SIG-CORE-001",
        intent_timestamp=base_timestamp,
    )
    diff_id = generate_idempotent_order_id(different_intent)
    assert diff_id != id1


def test_ac002_duplicate_submission_intercepted(
    sample_intent: OrderIntent,
    base_timestamp: datetime,
) -> None:
    """AC-002: Duplicate submission of identical intent is intercepted (Section 11.6)."""
    registry = IdempotentOrderRegistry()

    # First submission: Accepted
    receipt1 = evaluate_order_submission_idempotency(
        intent=sample_intent,
        registry=registry,
        evaluation_timestamp=base_timestamp,
    )
    assert receipt1.disposition == SubmissionDisposition.SUBMITTED_NEW
    assert receipt1.is_safe_to_submit is True
    assert enforce_submission_idempotency(receipt1) is True

    # Duplicate submission: Intercepted
    receipt2 = evaluate_order_submission_idempotency(
        intent=sample_intent,
        registry=registry,
        evaluation_timestamp=base_timestamp,
    )
    assert receipt2.disposition == SubmissionDisposition.DUPLICATE_INTERCEPTED
    assert receipt2.is_safe_to_submit is False

    # Enforcement strictly raises DuplicateOrderSubmissionError
    with pytest.raises(DuplicateOrderSubmissionError):
        enforce_submission_idempotency(receipt2)


def test_ac003_conflicting_order_payload_rejected(base_timestamp: datetime) -> None:
    """AC-003: Reusing an order identity with modified parameters raises conflict error."""
    registry = IdempotentOrderRegistry()

    intent_a = OrderIntent(
        symbol="MSFT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=50,
        signal_id="SIG-CORE-002",
        intent_timestamp=base_timestamp,
        client_order_id="ORD-EXPLICIT-1",
    )
    registry.evaluate_and_register(intent_a, base_timestamp)

    # Reusing ORD-EXPLICIT-1 with different quantity
    conflicting_intent = OrderIntent(
        symbol="MSFT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=75,  # Conflicting quantity
        signal_id="SIG-CORE-002",
        intent_timestamp=base_timestamp,
        client_order_id="ORD-EXPLICIT-1",
    )

    with pytest.raises(ConflictingOrderIdentityError):
        registry.evaluate_and_register(conflicting_intent, base_timestamp)


def test_ac004_conflicting_order_in_flight_blocked(
    sample_intent: OrderIntent,
    base_timestamp: datetime,
) -> None:
    """AC-004: In-flight active orders on the same symbol block new submissions (INV-010)."""
    registry = IdempotentOrderRegistry()

    # Active order already exists on broker feed for AAPL
    active_broker_order = BrokerOrderSnapshot(
        order_id="ORD-AAPL-EXISTING",
        broker_order_id="BRK-AAPL-999",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        filled_quantity=0,
        status=BrokerOrderStatus.SUBMITTED,
        timestamp=base_timestamp,
        is_terminal=False,
    )

    with pytest.raises(ConflictingOrderInFlightError, match="in-flight"):
        evaluate_order_submission_idempotency(
            intent=sample_intent,
            registry=registry,
            evaluation_timestamp=base_timestamp,
            active_broker_orders=[active_broker_order],
        )


def test_ac005_terminal_failure_allows_safe_retry(
    sample_intent: OrderIntent,
    base_timestamp: datetime,
) -> None:
    """AC-005: Once an order reaches terminal non-executed status, symbol lock is freed."""
    registry = IdempotentOrderRegistry()

    receipt1 = registry.evaluate_and_register(sample_intent, base_timestamp)
    order_id = receipt1.client_order_id
    assert registry.is_symbol_in_flight("AAPL") is True

    # Order is confirmed REJECTED on broker
    registry.update_order_status(order_id, BrokerOrderStatus.REJECTED)
    assert registry.is_symbol_in_flight("AAPL") is False

    # New order for AAPL with fresh signal/intent is now permitted
    fresh_intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        limit_price=151.0,
        signal_id="SIG-CORE-002",
        intent_timestamp=base_timestamp,
    )
    receipt2 = registry.evaluate_and_register(fresh_intent, base_timestamp)
    assert receipt2.disposition == SubmissionDisposition.SUBMITTED_NEW
    assert receipt2.is_safe_to_submit is True


def test_fail_closed_on_corrupt_unknown_intent(base_timestamp: datetime) -> None:
    """INV-008: Corrupt, non-numeric, NaN, or non-positive intent parameters fail closed."""
    registry = IdempotentOrderRegistry()

    # Non-positive quantity
    with pytest.raises(Exception):
        OrderIntent(
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0,
            signal_id="SIG-1",
            intent_timestamp=base_timestamp,
        )

    # NaN limit price
    with pytest.raises(Exception):
        OrderIntent(
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            limit_price=float("nan"),
            signal_id="SIG-1",
            intent_timestamp=base_timestamp,
        )

    # Empty symbol
    with pytest.raises(Exception):
        OrderIntent(
            symbol="",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            signal_id="SIG-1",
            intent_timestamp=base_timestamp,
        )

    # Invalid evaluation timestamp
    valid_intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        signal_id="SIG-1",
        intent_timestamp=base_timestamp,
    )
    with pytest.raises(UnknownBrokerOrderInputError):
        registry.evaluate_and_register(
            valid_intent,
            evaluation_timestamp="invalid-time",  # type: ignore[arg-type]
        )


def test_pure_determinism_and_immutable_receipts(
    sample_intent: OrderIntent,
    base_timestamp: datetime,
) -> None:
    """INV-001, INV-006: Receipts are immutable and evaluation is reproducible."""
    registry1 = IdempotentOrderRegistry()
    registry2 = IdempotentOrderRegistry()

    r1 = registry1.evaluate_and_register(sample_intent, base_timestamp)
    r2 = registry2.evaluate_and_register(sample_intent, base_timestamp)

    assert r1.client_order_id == r2.client_order_id
    assert r1.idempotency_key == r2.idempotency_key
    assert r1.disposition == r2.disposition
    assert r1.is_safe_to_submit == r2.is_safe_to_submit

    # Model immutability
    with pytest.raises(Exception):
        r1.is_safe_to_submit = False  # type: ignore[misc]
