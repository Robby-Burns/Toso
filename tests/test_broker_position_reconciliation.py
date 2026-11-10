"""Unit and integration tests for TOSO Broker Position Reconciliation (BROKER-003).

Verifies FR-013, INV-009, INV-010, INV-008, INV-001, INV-006:
- Pure determinism and immutable audit receipts.
- Detection of phantom broker positions, ghost local positions, and share count drift.
- Broker authority governance: broker state strictly governs actual positions (INV-009).
- Fail-closed gating on unacknowledged discrepancies or corrupted inputs (INV-008, INV-010).
- PortfolioStateSnapshot synchronization with authoritative position truth.
"""

from datetime import UTC, datetime

import pytest

from toso.broker.errors import (
    GhostPositionDetectedError,
    MaterialPositionDiscrepancyError,
    PhantomPositionDetectedError,
    UnknownBrokerPositionInputError,
)
from toso.broker.models import (
    BrokerPositionSnapshot,
    PositionDiscrepancyType,
    PositionReconciliationStatus,
)
from toso.broker.positions import (
    apply_reconciled_positions_to_portfolio_state,
    enforce_position_reconciliation,
    reconcile_positions,
)
from toso.guardian.models import CapitalSleeve, PortfolioStateSnapshot, PositionRiskSnapshot


@pytest.fixture
def base_timestamp() -> datetime:
    return datetime(2026, 10, 28, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def local_positions() -> list[PositionRiskSnapshot]:
    return [
        PositionRiskSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=150.0,
            stop_loss_price=140.0,
            open_risk_dollars=1000.0,
            market_value=15000.0,
            sleeve=CapitalSleeve.CORE,
        ),
        PositionRiskSnapshot(
            symbol="MSFT",
            shares=50,
            current_price=300.0,
            stop_loss_price=285.0,
            open_risk_dollars=750.0,
            market_value=15000.0,
            sleeve=CapitalSleeve.CORE,
        ),
    ]


@pytest.fixture
def broker_positions(base_timestamp: datetime) -> list[BrokerPositionSnapshot]:
    return [
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=150.0,
            market_value=15000.0,
            cost_basis=14500.0,
            timestamp=base_timestamp,
        ),
        BrokerPositionSnapshot(
            symbol="MSFT",
            shares=50,
            current_price=300.0,
            market_value=15000.0,
            cost_basis=29000.0,
            timestamp=base_timestamp,
        ),
    ]


def test_ac001_clean_reconciliation_when_matched(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """AC-001: Local and broker positions match exactly -> RECONCILED_CLEAN."""
    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=broker_positions,
        reconciliation_timestamp=base_timestamp,
    )

    assert receipt.status == PositionReconciliationStatus.RECONCILED_CLEAN
    assert not receipt.has_discrepancy
    assert not receipt.has_material_discrepancy
    assert len(receipt.discrepancies) == 0
    assert len(receipt.authoritative_positions) == 2
    assert receipt.timestamp == base_timestamp
    assert enforce_position_reconciliation(receipt) is True


def test_ac002_phantom_broker_position_detected(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """AC-002: Broker reports position unknown locally -> flags PHANTOM, imports (INV-009)."""
    phantom_broker_positions = list(broker_positions) + [
        BrokerPositionSnapshot(
            symbol="NVDA",
            shares=80,
            current_price=500.0,
            market_value=40000.0,
            cost_basis=38000.0,
            timestamp=base_timestamp,
        )
    ]

    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=phantom_broker_positions,
        reconciliation_timestamp=base_timestamp,
        default_stop_pct=0.05,
    )

    assert receipt.has_discrepancy
    assert receipt.has_material_discrepancy
    assert len(receipt.discrepancies) == 1
    assert (
        receipt.discrepancies[0].discrepancy_type == PositionDiscrepancyType.PHANTOM_BROKER_POSITION
    )
    assert receipt.discrepancies[0].symbol == "NVDA"
    assert receipt.discrepancies[0].broker_shares == 80
    assert receipt.discrepancies[0].local_shares == 0

    # Broker Authority (INV-009): NVDA must be imported into authoritative positions
    auth_symbols = [p.symbol for p in receipt.authoritative_positions]
    assert "NVDA" in auth_symbols
    assert len(receipt.authoritative_positions) == 3

    nvda_auth = next(p for p in receipt.authoritative_positions if p.symbol == "NVDA")
    assert nvda_auth.shares == 80
    assert nvda_auth.current_price == 500.0
    assert nvda_auth.market_value == 40000.0
    # 5% default stop: 500 * 0.95 = 475.0
    assert nvda_auth.stop_loss_price == 475.0
    assert nvda_auth.open_risk_dollars == round(80 * (500.0 - 475.0), 4)

    # Gating: must fail closed when unacknowledged
    with pytest.raises(PhantomPositionDetectedError):
        enforce_position_reconciliation(receipt, allow_broker_governed=False)

    # Allowed when broker authority acknowledged
    assert enforce_position_reconciliation(receipt, allow_broker_governed=True) is True


def test_ac003_ghost_local_position_detected(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """AC-003: Local records position missing on broker -> flags GHOST, drops position (INV-009)."""
    # Broker only has AAPL; MSFT is missing from broker (ghost position)
    ghost_broker_positions = [broker_positions[0]]

    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=ghost_broker_positions,
        reconciliation_timestamp=base_timestamp,
    )

    assert receipt.has_discrepancy
    assert receipt.has_material_discrepancy
    assert len(receipt.discrepancies) == 1
    assert receipt.discrepancies[0].discrepancy_type == PositionDiscrepancyType.GHOST_LOCAL_POSITION
    assert receipt.discrepancies[0].symbol == "MSFT"
    assert receipt.discrepancies[0].local_shares == 50
    assert receipt.discrepancies[0].broker_shares == 0

    # Broker Authority (INV-009): MSFT is omitted from authoritative positions
    auth_symbols = [p.symbol for p in receipt.authoritative_positions]
    assert "MSFT" not in auth_symbols
    assert auth_symbols == ["AAPL"]

    # Gating: must fail closed on ghost position when unacknowledged
    with pytest.raises(GhostPositionDetectedError):
        enforce_position_reconciliation(receipt, allow_broker_governed=False)

    # Allowed when acknowledged
    assert enforce_position_reconciliation(receipt, allow_broker_governed=True) is True


def test_ac004_share_quantity_mismatch_broker_governs(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """AC-004: Local shares != broker shares -> broker shares govern (INV-009)."""
    # Broker reports 150 shares for AAPL instead of 100
    drift_broker_positions = [
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=150,
            current_price=150.0,
            market_value=22500.0,
            cost_basis=21000.0,
            timestamp=base_timestamp,
        ),
        broker_positions[1],
    ]

    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=drift_broker_positions,
        reconciliation_timestamp=base_timestamp,
    )

    assert receipt.has_discrepancy
    assert receipt.has_material_discrepancy
    assert len(receipt.discrepancies) == 1
    assert receipt.discrepancies[0].discrepancy_type == PositionDiscrepancyType.QUANTITY_MISMATCH
    assert receipt.discrepancies[0].symbol == "AAPL"
    assert receipt.discrepancies[0].share_delta == 50

    # Broker Authority (INV-009): Authoritative AAPL adopts 150 shares
    aapl_auth = next(p for p in receipt.authoritative_positions if p.symbol == "AAPL")
    assert aapl_auth.shares == 150
    assert aapl_auth.market_value == 22500.0
    # Stop loss preserved from local ($140.0), risk = 150 * (150 - 140) = 1500.0
    assert aapl_auth.stop_loss_price == 140.0
    assert aapl_auth.open_risk_dollars == 1500.0

    # Gating: raises MaterialPositionDiscrepancyError when unacknowledged
    with pytest.raises(MaterialPositionDiscrepancyError):
        enforce_position_reconciliation(receipt, allow_broker_governed=False)

    assert enforce_position_reconciliation(receipt, allow_broker_governed=True) is True


def test_ac005_portfolio_state_synchronization(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """AC-005: PortfolioStateSnapshot is updated with authoritative positions (INV-009)."""
    old_state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=70000.0,
        positions=tuple(local_positions),
        timestamp=datetime(2026, 10, 28, 13, 0, 0, tzinfo=UTC),
    )

    # Broker reports an additional phantom position (AMZN)
    updated_broker_positions = list(broker_positions) + [
        BrokerPositionSnapshot(
            symbol="AMZN",
            shares=20,
            current_price=180.0,
            market_value=3600.0,
            cost_basis=3500.0,
            timestamp=base_timestamp,
        )
    ]

    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=updated_broker_positions,
        reconciliation_timestamp=base_timestamp,
    )

    # Apply to portfolio state
    synced_state = apply_reconciled_positions_to_portfolio_state(
        portfolio_state=old_state,
        reconciliation_receipt=receipt,
        update_equity_from_positions=True,
    )

    assert len(synced_state.positions) == 3
    assert {p.symbol for p in synced_state.positions} == {"AAPL", "MSFT", "AMZN"}
    assert synced_state.timestamp == base_timestamp
    # New equity: cash ($70000) + positions ($15000 + $15000 + $3600) = $103600
    assert synced_state.portfolio_equity == 103600.0
    assert synced_state.peak_portfolio_equity == 103600.0


def test_fail_closed_on_corrupt_unknown_inputs(
    local_positions: list[PositionRiskSnapshot],
    base_timestamp: datetime,
) -> None:
    """INV-008: Corrupt, non-numeric, NaN, infinite, or duplicate inputs fail closed."""
    # Corrupt price (NaN)
    with pytest.raises(Exception):
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=float("nan"),
            market_value=15000.0,
            timestamp=base_timestamp,
        )

    # Negative shares
    with pytest.raises(Exception):
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=-10,
            current_price=150.0,
            market_value=15000.0,
            timestamp=base_timestamp,
        )

    # Invalid timestamp
    with pytest.raises(UnknownBrokerPositionInputError):
        reconcile_positions(
            local_positions=local_positions,
            broker_positions=[],
            reconciliation_timestamp="invalid-timestamp",  # type: ignore[arg-type]
        )

    # Duplicate symbols in broker positions
    dup_broker = [
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=150.0,
            market_value=15000.0,
            timestamp=base_timestamp,
        ),
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=50,
            current_price=150.0,
            market_value=7500.0,
            timestamp=base_timestamp,
        ),
    ]
    with pytest.raises(UnknownBrokerPositionInputError, match="Duplicate symbols"):
        reconcile_positions(
            local_positions=local_positions,
            broker_positions=dup_broker,
            reconciliation_timestamp=base_timestamp,
        )


def test_pure_determinism_and_immutable_receipts(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """INV-001, INV-006: Re-running with identical inputs produces identical audit receipts."""
    receipt1 = reconcile_positions(
        local_positions=local_positions,
        broker_positions=broker_positions,
        reconciliation_timestamp=base_timestamp,
    )
    receipt2 = reconcile_positions(
        local_positions=local_positions,
        broker_positions=broker_positions,
        reconciliation_timestamp=base_timestamp,
    )

    assert receipt1.receipt_id == receipt2.receipt_id
    assert receipt1.timestamp == receipt2.timestamp
    assert receipt1.status == receipt2.status
    assert receipt1.authoritative_positions == receipt2.authoritative_positions
    assert receipt1.discrepancies == receipt2.discrepancies

    # Verify immutability
    with pytest.raises(Exception):
        receipt1.status = PositionReconciliationStatus.DISCREPANCY_DETECTED  # type: ignore[misc]


def test_market_value_drift_tolerance(
    local_positions: list[PositionRiskSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    base_timestamp: datetime,
) -> None:
    """Valuation drift exceeding tolerance is flagged as MARKET_VALUE_MISMATCH."""
    drift_broker_positions = [
        BrokerPositionSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=152.0,  # Price drifted from 150.0 to 152.0
            market_value=15200.0,  # Value drifted by $200
            cost_basis=14500.0,
            timestamp=base_timestamp,
        ),
        broker_positions[1],
    ]

    receipt = reconcile_positions(
        local_positions=local_positions,
        broker_positions=drift_broker_positions,
        reconciliation_timestamp=base_timestamp,
        tolerance_market_value=0.01,
    )

    assert receipt.has_discrepancy
    assert not receipt.has_material_discrepancy
    assert len(receipt.discrepancies) == 1
    assert (
        receipt.discrepancies[0].discrepancy_type == PositionDiscrepancyType.MARKET_VALUE_MISMATCH
    )
    assert receipt.discrepancies[0].value_delta == 200.0

    # Since it's non-material, enforce_position_reconciliation passes
    assert enforce_position_reconciliation(receipt, allow_broker_governed=False) is True
