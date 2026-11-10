"""Unit and integration tests for TOSO Black Swan Capital Allocation & Separation (BS-001).

Verifies FR-010, FR-011, INV-012, INV-013, INV-008, INV-001, INV-006:
- Strict capital partitioning: Black Swan capital is strictly isolated (FR-010).
- Core / Black Swan independence: defensive evaluation does not alter Core rules (FR-011).
- Gating against defensive capital exhaustion and sleeve breaches (INV-013).
- Fail-closed behavior on corrupted, NaN, or non-positive inputs (INV-008).
- Pure determinism and immutable audit receipts (INV-001, INV-006).
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import CapitalAllocationConfig
from toso.defense.capital import (
    calculate_black_swan_capital,
    enforce_black_swan_intake_safety,
    evaluate_black_swan_capital_intake,
)
from toso.defense.errors import (
    BlackSwanCapitalExhaustedError,
    BlackSwanSleeveBreachError,
    UnknownDefenseInputError,
)
from toso.defense.models import BlackSwanIntakeStatus
from toso.guardian.models import CapitalSleeve, PortfolioStateSnapshot, PositionRiskSnapshot


@pytest.fixture
def base_timestamp() -> datetime:
    return datetime(2026, 10, 28, 17, 0, 0, tzinfo=UTC)


@pytest.fixture
def capital_config() -> CapitalAllocationConfig:
    return CapitalAllocationConfig(
        core_allocation_pct=0.80,
        reserve_allocation_pct=0.15,
        black_swan_allocation_pct=0.05,
    )


@pytest.fixture
def base_portfolio_state(base_timestamp: datetime) -> PortfolioStateSnapshot:
    return PortfolioStateSnapshot(
        portfolio_equity=1_000_000.0,
        peak_portfolio_equity=1_000_000.0,
        cash_balance=200_000.0,
        positions=(
            PositionRiskSnapshot(
                symbol="AAPL",
                shares=1000,
                current_price=150.0,
                stop_loss_price=140.0,
                open_risk_dollars=10_000.0,
                market_value=150_000.0,
                sleeve=CapitalSleeve.CORE,
            ),
        ),
        timestamp=base_timestamp,
    )


def test_ac001_black_swan_capital_partitioning(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """AC-001: Black Swan capital is strictly partitioned to 5% of portfolio equity."""
    snapshot = calculate_black_swan_capital(
        portfolio_state=base_portfolio_state,
        config=capital_config,
        timestamp=base_timestamp,
    )

    # 5% of $1,000,000 = $50,000
    assert snapshot.portfolio_equity == 1_000_000.0
    assert snapshot.allocation_pct == 0.05
    assert snapshot.allocated_budget_dollars == 50_000.0
    assert snapshot.deployed_market_value_dollars == 0.0
    assert snapshot.available_capacity_dollars == 50_000.0
    assert snapshot.timestamp == base_timestamp


def test_ac002_core_trading_cannot_encroach_on_defense(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """AC-002: Core positions do not consume Black Swan defensive capacity."""
    # Portfolio has large Core position ($150,000)
    snapshot = calculate_black_swan_capital(
        portfolio_state=base_portfolio_state,
        config=capital_config,
        timestamp=base_timestamp,
    )

    # Black Swan capacity remains completely intact at $50,000
    assert snapshot.available_capacity_dollars == 50_000.0
    assert snapshot.deployed_market_value_dollars == 0.0


def test_ac003_defensive_intake_within_capacity(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """AC-003: Proposed defensive allocation within available capacity is approved."""
    # Propose $20,000 tail risk defense (e.g. VIX calls) out of $50,000 capacity
    assessment = evaluate_black_swan_capital_intake(
        portfolio_state=base_portfolio_state,
        proposed_market_value=20_000.0,
        config=capital_config,
        evaluation_timestamp=base_timestamp,
    )

    assert assessment.status == BlackSwanIntakeStatus.APPROVED
    assert assessment.is_approved is True
    assert assessment.proposed_market_value == 20_000.0
    assert assessment.capital_snapshot.available_capacity_dollars == 50_000.0
    assert enforce_black_swan_intake_safety(assessment) is True


def test_ac004_defense_intake_exceeding_capacity_rejected(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """AC-004: Proposed defensive allocation exceeding capacity is strictly rejected."""
    # Propose $60,000 defensive trade exceeding $50,000 capacity
    assessment = evaluate_black_swan_capital_intake(
        portfolio_state=base_portfolio_state,
        proposed_market_value=60_000.0,
        config=capital_config,
        evaluation_timestamp=base_timestamp,
    )

    assert assessment.status == BlackSwanIntakeStatus.CAPITAL_EXHAUSTED
    assert assessment.is_approved is False

    with pytest.raises(BlackSwanCapitalExhaustedError):
        enforce_black_swan_intake_safety(assessment)


def test_ac005_target_sleeve_breach_rejected(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """AC-005: Attempting defensive intake targeting non-defense sleeves is rejected."""
    # Target CORE sleeve for a defensive allocation
    assessment = evaluate_black_swan_capital_intake(
        portfolio_state=base_portfolio_state,
        proposed_market_value=10_000.0,
        config=capital_config,
        evaluation_timestamp=base_timestamp,
        target_sleeve=CapitalSleeve.CORE,
    )

    assert assessment.status == BlackSwanIntakeStatus.SLEEVE_BREACH
    assert assessment.is_approved is False

    with pytest.raises(BlackSwanSleeveBreachError):
        enforce_black_swan_intake_safety(assessment)


def test_ac006_deployed_defense_reduces_capacity_accurately(
    base_timestamp: datetime,
    capital_config: CapitalAllocationConfig,
) -> None:
    """AC-006: Active Black Swan positions reduce available defensive capacity."""
    portfolio_with_defense = PortfolioStateSnapshot(
        portfolio_equity=1_000_000.0,
        peak_portfolio_equity=1_000_000.0,
        cash_balance=200_000.0,
        positions=(
            PositionRiskSnapshot(
                symbol="AAPL",
                shares=1000,
                current_price=150.0,
                stop_loss_price=140.0,
                open_risk_dollars=10_000.0,
                market_value=150_000.0,
                sleeve=CapitalSleeve.CORE,
            ),
            PositionRiskSnapshot(
                symbol="UVXY",
                shares=500,
                current_price=40.0,
                stop_loss_price=35.0,
                open_risk_dollars=2500.0,
                market_value=20_000.0,
                sleeve=CapitalSleeve.BLACK_SWAN,
            ),
        ),
        timestamp=base_timestamp,
    )

    snapshot = calculate_black_swan_capital(
        portfolio_state=portfolio_with_defense,
        config=capital_config,
        timestamp=base_timestamp,
    )

    assert snapshot.allocated_budget_dollars == 50_000.0
    assert snapshot.deployed_market_value_dollars == 20_000.0
    assert snapshot.available_capacity_dollars == 30_000.0


def test_fail_closed_on_corrupt_unknown_inputs(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """INV-008: NaN, negative, or invalid inputs fail closed."""
    # Negative proposed market value
    with pytest.raises(UnknownDefenseInputError):
        evaluate_black_swan_capital_intake(
            portfolio_state=base_portfolio_state,
            proposed_market_value=-100.0,
            config=capital_config,
            evaluation_timestamp=base_timestamp,
        )

    # NaN proposed market value
    with pytest.raises(UnknownDefenseInputError):
        evaluate_black_swan_capital_intake(
            portfolio_state=base_portfolio_state,
            proposed_market_value=float("nan"),
            config=capital_config,
            evaluation_timestamp=base_timestamp,
        )

    # Invalid timestamp
    with pytest.raises(UnknownDefenseInputError):
        calculate_black_swan_capital(
            portfolio_state=base_portfolio_state,
            config=capital_config,
            timestamp="invalid-timestamp",  # type: ignore[arg-type]
        )


def test_pure_determinism_and_immutable_receipts(
    base_portfolio_state: PortfolioStateSnapshot,
    capital_config: CapitalAllocationConfig,
    base_timestamp: datetime,
) -> None:
    """INV-001, INV-006: Identical evaluations produce identical receipts; models are frozen."""
    assessment1 = evaluate_black_swan_capital_intake(
        portfolio_state=base_portfolio_state,
        proposed_market_value=25_000.0,
        config=capital_config,
        evaluation_timestamp=base_timestamp,
    )
    assessment2 = evaluate_black_swan_capital_intake(
        portfolio_state=base_portfolio_state,
        proposed_market_value=25_000.0,
        config=capital_config,
        evaluation_timestamp=base_timestamp,
    )

    assert assessment1.assessment_id == assessment2.assessment_id
    assert assessment1.status == assessment2.status
    assert assessment1.capital_snapshot == assessment2.capital_snapshot
    assert assessment1.diagnostic_reasons == assessment2.diagnostic_reasons

    # Immutability
    with pytest.raises(Exception):
        assessment1.is_approved = False  # type: ignore[misc]
