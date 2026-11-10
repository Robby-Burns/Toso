"""Verification Test Suite for Story BROKER-001: Account Reconciliation.

Verifies:
- FR-013: Broker state is authoritative for account balances and equity.
- INV-009: When local state conflicts with broker state, broker state governs.
- INV-010: Ambiguous outcomes must be reconciled before action (no blind retry).
- INV-008: Fail-closed behavior on missing, stale, or malformed broker data.
- INV-001 & INV-006: Pure determinism and immutable audit receipts.
"""

from datetime import UTC, datetime

import pytest

from toso.broker.account import (
    _validate_float,
    apply_reconciled_account_to_portfolio_state,
    enforce_account_reconciliation,
    reconcile_account_state,
)
from toso.broker.errors import (
    BrokerDisconnectedError,
    MaterialAccountDiscrepancyError,
    StaleBrokerAccountDataError,
    UnknownBrokerAccountInputError,
)
from toso.broker.models import (
    AccountDiscrepancyType,
    AccountReconciliationStatus,
    BrokerAccountSnapshot,
    LocalAccountSnapshot,
)
from toso.guardian.models import (
    CapitalSleeve,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
)


@pytest.fixture
def eval_timestamp() -> datetime:
    return datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def valid_broker_snapshot(eval_timestamp: datetime) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        buying_power=90000.0,
        currency="USD",
        timestamp=eval_timestamp,
        is_connected=True,
    )


@pytest.fixture
def valid_local_snapshot(eval_timestamp: datetime) -> LocalAccountSnapshot:
    return LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        currency="USD",
        timestamp=eval_timestamp,
    )


@pytest.fixture
def sample_portfolio_state(eval_timestamp: datetime) -> PortfolioStateSnapshot:
    pos = PositionRiskSnapshot(
        symbol="AAPL",
        shares=100,
        current_price=160.0,
        stop_loss_price=150.0,
        open_risk_dollars=1000.0,
        market_value=16000.0,
        sleeve=CapitalSleeve.CORE,
    )
    return PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=45000.0,
        positions=(pos,),
        timestamp=eval_timestamp,
    )


def test_ac001_clean_reconciliation_when_matched(
    valid_broker_snapshot: BrokerAccountSnapshot,
    valid_local_snapshot: LocalAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-001: Local state matching broker state results in clean RECONCILED receipt."""
    receipt = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == AccountReconciliationStatus.RECONCILED
    assert receipt.is_synchronized is True
    assert len(receipt.discrepancies) == 0
    assert receipt.authoritative_equity == 100000.0
    assert receipt.authoritative_cash == 45000.0
    assert receipt.timestamp == eval_timestamp

    # Enforce should succeed quietly
    enforce_account_reconciliation(receipt)


def test_ac001_discrepancy_detected_when_unmatched(
    valid_broker_snapshot: BrokerAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-001: Mismatched equity detects discrepancy and fails enforcement."""
    # Local thinks equity is $105,000, but broker reports $100,000
    divergent_local = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=105000.0,
        cash_balance=45000.0,
        currency="USD",
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=divergent_local,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED
    assert receipt.is_synchronized is False
    assert len(receipt.discrepancies) == 1
    assert receipt.discrepancies[0].discrepancy_type == AccountDiscrepancyType.EQUITY_MISMATCH
    assert receipt.discrepancies[0].delta == -5000.0
    assert receipt.authoritative_equity == 100000.0  # Broker governs (INV-009)!

    with pytest.raises(MaterialAccountDiscrepancyError) as exc_info:
        enforce_account_reconciliation(receipt)
    assert "Discrepancy detected" in str(exc_info.value)


def test_ac001_cash_mismatch_detected(
    valid_broker_snapshot: BrokerAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-001: Cash balance mismatch is detected as a material discrepancy."""
    # Local thinks cash is $50,000, broker reports $45,000
    divergent_cash_local = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.0,
        cash_balance=50000.0,
        currency="USD",
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=divergent_cash_local,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED
    assert receipt.is_synchronized is False
    assert any(
        d.discrepancy_type == AccountDiscrepancyType.CASH_MISMATCH for d in receipt.discrepancies
    )
    assert receipt.authoritative_cash == 45000.0  # Broker governs!

    with pytest.raises(MaterialAccountDiscrepancyError):
        enforce_account_reconciliation(receipt)


def test_ac002_fail_closed_on_stale_or_disconnected_broker(
    valid_broker_snapshot: BrokerAccountSnapshot,
    valid_local_snapshot: LocalAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-002 / INV-010: Offline or stale broker state fails closed; no blind retry."""
    # 1. Disconnected Broker
    disconnected_broker = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        buying_power=90000.0,
        currency="USD",
        timestamp=eval_timestamp,
        is_connected=False,
    )
    receipt_offline = reconcile_account_state(
        broker_snapshot=disconnected_broker,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )
    assert receipt_offline.status == AccountReconciliationStatus.BROKER_UNAVAILABLE
    with pytest.raises(BrokerDisconnectedError):
        enforce_account_reconciliation(receipt_offline)

    # 2. Stale Broker Snapshot (age 120s > max 60s)
    stale_ts = datetime(2026, 10, 28, 11, 57, 0, tzinfo=UTC)
    stale_broker = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        buying_power=90000.0,
        currency="USD",
        timestamp=stale_ts,
        is_connected=True,
    )
    receipt_stale = reconcile_account_state(
        broker_snapshot=stale_broker,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
        max_staleness_seconds=60.0,
    )
    assert receipt_stale.status == AccountReconciliationStatus.STALE_SNAPSHOT
    with pytest.raises(StaleBrokerAccountDataError):
        enforce_account_reconciliation(receipt_stale)

    # 3. Future Timestamp
    future_ts = datetime(2026, 10, 28, 12, 5, 0, tzinfo=UTC)
    future_broker = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        buying_power=90000.0,
        currency="USD",
        timestamp=future_ts,
        is_connected=True,
    )
    receipt_future = reconcile_account_state(
        broker_snapshot=future_broker,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )
    assert receipt_future.status == AccountReconciliationStatus.STALE_SNAPSHOT
    with pytest.raises(StaleBrokerAccountDataError):
        enforce_account_reconciliation(receipt_future)


def test_ac003_broker_authority_governs_local_state(
    eval_timestamp: datetime,
) -> None:
    """Verify AC-003 & INV-009: When conflict occurs, broker state governs actual truth."""
    broker_snapshot = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=92000.0,  # Broker confirms equity is 92k
        cash_balance=38000.0,  # Broker confirms cash is 38k
        buying_power=76000.0,
        currency="USD",
        timestamp=eval_timestamp,
        is_connected=True,
    )
    local_snapshot = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.0,  # Local mistakenly thought 100k
        cash_balance=45000.0,  # Local mistakenly thought 45k
        currency="USD",
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=broker_snapshot,
        local_snapshot=local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )

    # Even though a discrepancy is detected, the authoritative numbers in the receipt
    # strictly adopt the broker values (INV-009)
    assert receipt.authoritative_equity == 92000.0
    assert receipt.authoritative_cash == 38000.0
    assert receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED


def test_ac003_portfolio_state_synchronization(
    sample_portfolio_state: PortfolioStateSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify AC-003: Reconciled broker truth synchronizes into PortfolioStateSnapshot."""
    broker_snapshot = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=110000.0,  # Higher equity setting new peak
        cash_balance=55000.0,
        buying_power=110000.0,
        currency="USD",
        timestamp=eval_timestamp,
        is_connected=True,
    )
    local_snapshot = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        currency="USD",
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=broker_snapshot,
        local_snapshot=local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )

    updated_portfolio = apply_reconciled_account_to_portfolio_state(
        portfolio_state=sample_portfolio_state,
        reconciliation_receipt=receipt,
    )

    assert updated_portfolio.portfolio_equity == 110000.0
    assert updated_portfolio.peak_portfolio_equity == 110000.0  # High-water mark raised
    assert updated_portfolio.cash_balance == 55000.0
    assert updated_portfolio.positions == sample_portfolio_state.positions  # Positions preserved
    assert updated_portfolio.timestamp == eval_timestamp


def test_tolerance_immaterial_differences(
    eval_timestamp: datetime,
) -> None:
    """Verify that differences under tolerance (e.g. $0.005) pass as RECONCILED."""
    broker_snapshot = BrokerAccountSnapshot(
        account_id="ACC-BROKER-001",
        total_equity=100000.004,
        cash_balance=45000.002,
        buying_power=90000.0,
        currency="USD",
        timestamp=eval_timestamp,
        is_connected=True,
    )
    local_snapshot = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.00,
        cash_balance=45000.00,
        currency="USD",
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=broker_snapshot,
        local_snapshot=local_snapshot,
        reconciliation_timestamp=eval_timestamp,
        tolerance_dollars=0.01,
    )

    assert receipt.status == AccountReconciliationStatus.RECONCILED
    assert receipt.is_synchronized is True


def test_currency_mismatch_fails_closed(
    valid_broker_snapshot: BrokerAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify that currency mismatches fail closed with DISCREPANCY_DETECTED."""
    local_eur = LocalAccountSnapshot(
        account_id="ACC-LOCAL-001",
        total_equity=100000.0,
        cash_balance=45000.0,
        currency="EUR",  # Differs from USD
        timestamp=eval_timestamp,
    )

    receipt = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=local_eur,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED
    assert any(
        d.discrepancy_type == AccountDiscrepancyType.CURRENCY_MISMATCH
        for d in receipt.discrepancies
    )


def test_fail_closed_on_corrupt_unknown_inputs(
    valid_broker_snapshot: BrokerAccountSnapshot,
    valid_local_snapshot: LocalAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify INV-008: Corrupted or non-numeric float values fail closed."""
    with pytest.raises(UnknownBrokerAccountInputError):
        _validate_float(float("nan"), "test_nan")

    with pytest.raises(UnknownBrokerAccountInputError):
        _validate_float(float("inf"), "test_inf")

    with pytest.raises(UnknownBrokerAccountInputError):
        _validate_float(-10.0, "test_neg", must_be_positive=True)

    with pytest.raises(UnknownBrokerAccountInputError):
        _validate_float("not-numeric", "test_str")  # type: ignore

    # Non-datetime timestamp
    with pytest.raises(UnknownBrokerAccountInputError):
        reconcile_account_state(
            broker_snapshot=valid_broker_snapshot,
            local_snapshot=valid_local_snapshot,
            reconciliation_timestamp="invalid-timestamp",  # type: ignore
        )


def test_pure_determinism_and_immutable_receipts(
    valid_broker_snapshot: BrokerAccountSnapshot,
    valid_local_snapshot: LocalAccountSnapshot,
    eval_timestamp: datetime,
) -> None:
    """Verify INV-001 & INV-006: Identical inputs produce identical receipts; immutable models."""
    receipt1 = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )
    receipt2 = reconcile_account_state(
        broker_snapshot=valid_broker_snapshot,
        local_snapshot=valid_local_snapshot,
        reconciliation_timestamp=eval_timestamp,
    )

    assert receipt1 == receipt2
    assert receipt1.receipt_id == receipt2.receipt_id

    # Immutability check
    with pytest.raises(Exception):
        receipt1.status = AccountReconciliationStatus.FAILED_CLOSED  # type: ignore
