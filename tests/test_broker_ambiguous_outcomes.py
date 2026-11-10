"""Unit and integration tests for TOSO Ambiguous Outcome Handling & No Blind Retry (BROKER-004).

Verifies FR-013, INV-010, INV-009, INV-008, INV-001, INV-006:
- Ambiguous broker outcomes are reconciled before any retry or action (FR-013, INV-010).
- Strict No-Blind-Retry: retries of ambiguous or active orders are prohibited (INV-010).
- Confirmed broker states strictly govern fill adoption and tracking (INV-009).
- Unresolvable queries fail closed with symbol-level action locks (INV-008, INV-010).
- Pure determinism and immutable audit receipts without wall-clock reads (INV-001, INV-006).
"""

from datetime import UTC, datetime

import pytest

from toso.broker.ambiguity import (
    enforce_no_blind_retry,
    enforce_symbol_action_safety,
    resolve_ambiguity,
)
from toso.broker.errors import (
    ConflictingActionBlockedError,
    OrderAlreadyExecutedError,
    OrderDiscrepancyUnresolvableError,
    UnknownBrokerOrderInputError,
    UnresolvedAmbiguityError,
)
from toso.broker.models import (
    AmbiguityResolutionAction,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    OrderSide,
    OrderType,
)


@pytest.fixture
def base_timestamp() -> datetime:
    return datetime(2026, 10, 28, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_broker_fill(base_timestamp: datetime) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        order_id="ORD-101",
        broker_order_id="BRK-999",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
        filled_quantity=100,
        average_fill_price=150.25,
        status=BrokerOrderStatus.FILLED,
        timestamp=base_timestamp,
        is_terminal=True,
    )


@pytest.fixture
def sample_broker_active(base_timestamp: datetime) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        order_id="ORD-102",
        broker_order_id="BRK-1000",
        symbol="MSFT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        filled_quantity=0,
        average_fill_price=None,
        status=BrokerOrderStatus.SUBMITTED,
        timestamp=base_timestamp,
        is_terminal=False,
    )


def test_ac001_ambiguity_resolved_to_fill(
    sample_broker_fill: BrokerOrderSnapshot,
    base_timestamp: datetime,
) -> None:
    """AC-001: Inquiry confirms fill -> adopts fill, blocks retry (INV-009, INV-010)."""
    receipt = resolve_ambiguity(
        order_id="ORD-101",
        symbol="AAPL",
        broker_order=sample_broker_fill,
        resolution_timestamp=base_timestamp,
        is_query_successful=True,
    )

    assert receipt.is_resolved is True
    assert receipt.action == AmbiguityResolutionAction.ADOPT_FILL
    assert receipt.can_retry is False
    assert receipt.prohibit_duplicate_execution is True
    assert receipt.authoritative_order == sample_broker_fill
    assert receipt.timestamp == base_timestamp

    # Strict No-Blind-Retry: duplicate execution on filled order is prohibited
    with pytest.raises(OrderAlreadyExecutedError):
        enforce_no_blind_retry(receipt)


def test_ac002_ambiguity_resolved_to_active_order(
    sample_broker_active: BrokerOrderSnapshot,
    base_timestamp: datetime,
) -> None:
    """AC-002: Inquiry finds active order -> tracks active, blocks duplicate (INV-010)."""
    receipt = resolve_ambiguity(
        order_id="ORD-102",
        symbol="MSFT",
        broker_order=sample_broker_active,
        resolution_timestamp=base_timestamp,
        is_query_successful=True,
    )

    assert receipt.is_resolved is True
    assert receipt.action == AmbiguityResolutionAction.TRACK_ACTIVE
    assert receipt.can_retry is False
    assert receipt.prohibit_duplicate_execution is True

    with pytest.raises(OrderAlreadyExecutedError):
        enforce_no_blind_retry(receipt)


def test_ac003_ambiguity_resolved_to_dropped(base_timestamp: datetime) -> None:
    """AC-003: Broker confirms order never received -> dropped, safe to retry (INV-010)."""
    receipt = resolve_ambiguity(
        order_id="ORD-103",
        symbol="NVDA",
        broker_order=None,  # Confirmed not found
        resolution_timestamp=base_timestamp,
        is_query_successful=True,
    )

    assert receipt.is_resolved is True
    assert receipt.action == AmbiguityResolutionAction.CONFIRM_DROPPED
    assert receipt.can_retry is True
    assert receipt.prohibit_duplicate_execution is False

    # Safe to resubmit with fresh identity
    assert enforce_no_blind_retry(receipt) is True


def test_ac004_ambiguity_resolved_to_terminal_failure(base_timestamp: datetime) -> None:
    """AC-004: Broker reports rejected/canceled order -> terminal failure, permits retry."""
    rejected_order = BrokerOrderSnapshot(
        order_id="ORD-104",
        broker_order_id="BRK-1004",
        symbol="TSLA",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=30,
        filled_quantity=0,
        average_fill_price=None,
        status=BrokerOrderStatus.REJECTED,
        timestamp=base_timestamp,
        is_terminal=True,
    )

    receipt = resolve_ambiguity(
        order_id="ORD-104",
        symbol="TSLA",
        broker_order=rejected_order,
        resolution_timestamp=base_timestamp,
        is_query_successful=True,
    )

    assert receipt.is_resolved is True
    assert receipt.action == AmbiguityResolutionAction.PERMIT_RETRY
    assert receipt.can_retry is True
    assert receipt.prohibit_duplicate_execution is False

    assert enforce_no_blind_retry(receipt) is True


def test_ac005_unresolvable_ambiguity_fails_closed(base_timestamp: datetime) -> None:
    """AC-005: Inquiry times out or broker feed unreachable -> fails closed (INV-008, INV-010)."""
    receipt = resolve_ambiguity(
        order_id="ORD-105",
        symbol="GOOGL",
        broker_order=None,
        resolution_timestamp=base_timestamp,
        is_query_successful=False,  # Timed out / failed
    )

    assert receipt.is_resolved is False
    assert receipt.action == AmbiguityResolutionAction.HOLD_UNRESOLVED
    assert receipt.can_retry is False
    assert receipt.prohibit_duplicate_execution is True

    # Blind retry is strictly prohibited
    with pytest.raises(UnresolvedAmbiguityError):
        enforce_no_blind_retry(receipt)


def test_ac006_symbol_level_lockout_on_unresolved_ambiguity(
    base_timestamp: datetime,
) -> None:
    """AC-006: New orders for a symbol are blocked while an ambiguous order remains pending."""
    unresolved_receipt = resolve_ambiguity(
        order_id="ORD-106",
        symbol="AMZN",
        broker_order=None,
        resolution_timestamp=base_timestamp,
        is_query_successful=False,
    )

    # Action on AMZN is blocked
    with pytest.raises(ConflictingActionBlockedError, match="Conflicting action blocked for AMZN"):
        enforce_symbol_action_safety("AMZN", [unresolved_receipt])

    # Unrelated symbol is permitted
    assert enforce_symbol_action_safety("META", [unresolved_receipt]) is True

    # Once resolved, AMZN is unblocked
    resolved_receipt = resolve_ambiguity(
        order_id="ORD-106",
        symbol="AMZN",
        broker_order=None,
        resolution_timestamp=base_timestamp,
        is_query_successful=True,  # Dropped
    )
    assert enforce_symbol_action_safety("AMZN", [resolved_receipt]) is True


def test_ac007_identity_mismatch_raises_unresolvable_error(
    sample_broker_fill: BrokerOrderSnapshot,
    base_timestamp: datetime,
) -> None:
    """Mismatched order ID or symbol in inquiry response raises unresolvable discrepancy."""
    with pytest.raises(OrderDiscrepancyUnresolvableError):
        resolve_ambiguity(
            order_id="ORD-DIFFERENT",
            symbol="AAPL",
            broker_order=sample_broker_fill,
            resolution_timestamp=base_timestamp,
        )

    with pytest.raises(OrderDiscrepancyUnresolvableError):
        resolve_ambiguity(
            order_id="ORD-101",
            symbol="WRONG_SYMBOL",
            broker_order=sample_broker_fill,
            resolution_timestamp=base_timestamp,
        )


def test_fail_closed_on_corrupt_unknown_inputs(base_timestamp: datetime) -> None:
    """INV-008: Empty order ID, empty symbol, or invalid timestamp fail closed."""
    with pytest.raises(UnknownBrokerOrderInputError):
        resolve_ambiguity(
            order_id="",
            symbol="AAPL",
            broker_order=None,
            resolution_timestamp=base_timestamp,
        )

    with pytest.raises(UnknownBrokerOrderInputError):
        resolve_ambiguity(
            order_id="ORD-100",
            symbol="",
            broker_order=None,
            resolution_timestamp=base_timestamp,
        )

    with pytest.raises(UnknownBrokerOrderInputError):
        resolve_ambiguity(
            order_id="ORD-100",
            symbol="AAPL",
            broker_order=None,
            resolution_timestamp="not-a-datetime",  # type: ignore[arg-type]
        )


def test_pure_determinism_and_immutable_receipts(
    sample_broker_fill: BrokerOrderSnapshot,
    base_timestamp: datetime,
) -> None:
    """INV-001, INV-006: Pure determinism and frozen immutability across evaluation runs."""
    receipt1 = resolve_ambiguity(
        order_id="ORD-101",
        symbol="AAPL",
        broker_order=sample_broker_fill,
        resolution_timestamp=base_timestamp,
    )
    receipt2 = resolve_ambiguity(
        order_id="ORD-101",
        symbol="AAPL",
        broker_order=sample_broker_fill,
        resolution_timestamp=base_timestamp,
    )

    assert receipt1.receipt_id == receipt2.receipt_id
    assert receipt1.timestamp == receipt2.timestamp
    assert receipt1.action == receipt2.action
    assert receipt1.is_resolved == receipt2.is_resolved
    assert receipt1.authoritative_order == receipt2.authoritative_order
    assert receipt1.diagnostic_reasons == receipt2.diagnostic_reasons

    # Verify immutability
    with pytest.raises(Exception):
        receipt1.is_resolved = False  # type: ignore[misc]
