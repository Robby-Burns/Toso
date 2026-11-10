"""Tests for TOSO Guardian Capital Separation & Reserve Isolation (GUARD-003).

Verifies:
- FR-010, FR-011: Strict capital partitioning: Core (80%), Reserve (15%), Black Swan (5%).
- INV-012, INV-013: Reserve and Black Swan isolation; zero unauthorized encroachment.
- INV-001, INV-006: Deterministic execution, pure audit receipts, clock anchor echoing.
- INV-008: Fail-closed handling for UNKNOWN, non-positive, NaN, or infinite inputs.
- FINDING-001: 4-tier decision hierarchy (CORE_EXHAUSTED -> BLACK_SWAN -> RESERVE -> APPROVED).
- FINDING-002: Sleeve identification and non-Core position filtering.
- FINDING-004 / MISSING-001: Organic appreciation protection without liquidation.
- FINDING-005, FINDING-006: Independence from broker-level cash balance and buying power.
- MISSING-002: Non-Core target sleeve rejection.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toso.config.models import CapitalAllocationConfig
from toso.guardian.capital import (
    calculate_sleeve_allocations,
    enforce_capital_intake,
    evaluate_capital_intake,
)
from toso.guardian.errors import (
    BlackSwanEncroachmentError,
    CoreCapitalExhaustedError,
    ReserveEncroachmentError,
    UnknownCapitalInputError,
)
from toso.guardian.models import (
    CapitalSleeve,
    CapitalSleeveStatus,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProposedExposure,
)


@pytest.fixture
def default_capital_config() -> CapitalAllocationConfig:
    return CapitalAllocationConfig(
        core_allocation_pct=0.80,
        reserve_allocation_pct=0.15,
        black_swan_allocation_pct=0.05,
    )


@pytest.fixture
def sample_timestamp() -> datetime:
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def test_calculate_sleeve_allocations(default_capital_config: CapitalAllocationConfig) -> None:
    """Verify standard dollar allocations for $100k equity."""
    core, reserve, black_swan = calculate_sleeve_allocations(100_000.0, default_capital_config)
    assert core == 80_000.0
    assert reserve == 15_000.0
    assert black_swan == 5_000.0


def test_calculate_sleeve_allocations_fail_closed(
    default_capital_config: CapitalAllocationConfig,
) -> None:
    """Verify non-positive or NaN equity fails closed."""
    with pytest.raises(UnknownCapitalInputError):
        calculate_sleeve_allocations(0.0, default_capital_config)

    with pytest.raises(UnknownCapitalInputError):
        calculate_sleeve_allocations(-1000.0, default_capital_config)

    with pytest.raises(UnknownCapitalInputError):
        calculate_sleeve_allocations(float("nan"), default_capital_config)


def test_ac001_core_capacity_boundary_and_rejection(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Boundary & Functional Gating.

    Exact Core ceiling ($80,000 out of $100,000 equity) is APPROVED.
    Breach by $0.01 ($80,000.01) is BLOCKED with ReserveEncroachmentError.
    """
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        timestamp=sample_timestamp,
    )

    # 1. Exact boundary: $80,000.00
    boundary_exposure = ProposedExposure(
        symbol="AAPL",
        additional_shares=800,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=4_000.0,
        effective_market_value=80_000.0,
    )
    assessment = evaluate_capital_intake(portfolio_state, boundary_exposure, default_capital_config)
    assert assessment.is_approved is True
    assert assessment.status == CapitalSleeveStatus.APPROVED
    assert assessment.post_core_deployed_dollars == 80_000.0
    assert assessment.available_core_dollars == 80_000.0

    # Enforcement passes without exception
    enforce_res = enforce_capital_intake(portfolio_state, boundary_exposure, default_capital_config)
    assert enforce_res.is_approved is True

    # 2. Breach boundary: $80,000.01
    breach_exposure = ProposedExposure(
        symbol="AAPL",
        additional_shares=800,
        entry_price=100.0000125,
        stop_loss_price=95.0,
        effective_risk_dollars=4_000.0,
        effective_market_value=80_000.01,
    )
    breach_assessment = evaluate_capital_intake(
        portfolio_state, breach_exposure, default_capital_config
    )
    assert breach_assessment.is_approved is False
    assert breach_assessment.status == CapitalSleeveStatus.BLOCKED_RESERVE_ENCROACHMENT
    assert "encroaches into Reserve capital" in breach_assessment.rejection_reason

    with pytest.raises(ReserveEncroachmentError, match="encroaches into Reserve capital"):
        enforce_capital_intake(portfolio_state, breach_exposure, default_capital_config)


def test_ac002_decision_hierarchy_and_black_swan_encroachment(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Hierarchy & Precedence (FINDING-001).

    Equity = $100,000. Core capacity = $80,000. Reserve = $15,000. Black Swan = $5,000.
    Core + Reserve threshold = $95,000.
    1. Trade resulting in $90k Core encroaches Reserve ($80k < $90k <= $95k) -> RESERVE.
    2. Trade resulting in $96k Core encroaches Black Swan ($96k > $95k) -> BLACK_SWAN.
    """
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        timestamp=sample_timestamp,
    )

    # 1. Encroach into Reserve only ($90,000)
    reserve_exposure = ProposedExposure(
        symbol="MSFT",
        additional_shares=900,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=4_500.0,
        effective_market_value=90_000.0,
    )
    res_assessment = evaluate_capital_intake(
        portfolio_state, reserve_exposure, default_capital_config
    )
    assert res_assessment.status == CapitalSleeveStatus.BLOCKED_RESERVE_ENCROACHMENT
    with pytest.raises(ReserveEncroachmentError):
        enforce_capital_intake(portfolio_state, reserve_exposure, default_capital_config)

    # 2. Encroach past Reserve into Black Swan ($96,000)
    black_swan_exposure = ProposedExposure(
        symbol="NVDA",
        additional_shares=960,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=4_800.0,
        effective_market_value=96_000.0,
    )
    bs_assessment = evaluate_capital_intake(
        portfolio_state, black_swan_exposure, default_capital_config
    )
    assert bs_assessment.status == CapitalSleeveStatus.BLOCKED_BLACK_SWAN_ENCROACHMENT
    assert "encroaches into Black Swan capital" in bs_assessment.rejection_reason
    with pytest.raises(BlackSwanEncroachmentError, match="encroaches into Black Swan capital"):
        enforce_capital_intake(portfolio_state, black_swan_exposure, default_capital_config)


def test_ac003_core_exhausted_and_organic_appreciation(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Organic Appreciation & Immunity (FINDING-004, MISSING-001).

    Existing Core position appreciated from $70,000 to $85,000 (Core capacity is $80,000).
    Existing position is preserved (no force liquidation).
    Any new intake is BLOCKED with BLOCKED_CORE_EXHAUSTED and CoreCapitalExhaustedError.
    """
    appreciated_position = PositionRiskSnapshot(
        symbol="AAPL",
        shares=850,
        current_price=100.0,
        stop_loss_price=90.0,
        open_risk_dollars=8_500.0,
        market_value=85_000.0,
        sleeve=CapitalSleeve.CORE,
    )
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=15_000.0,
        positions=(appreciated_position,),
        timestamp=sample_timestamp,
    )

    new_exposure = ProposedExposure(
        symbol="GOOGL",
        additional_shares=10,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=50.0,
        effective_market_value=1_000.0,
    )

    assessment = evaluate_capital_intake(portfolio_state, new_exposure, default_capital_config)
    assert assessment.is_approved is False
    assert assessment.status == CapitalSleeveStatus.BLOCKED_CORE_EXHAUSTED
    assert assessment.available_core_dollars == -5_000.0  # Headroom is negative
    assert assessment.current_core_deployed_dollars == 85_000.0
    assert "Core capital exhausted" in assessment.rejection_reason

    with pytest.raises(CoreCapitalExhaustedError, match="Core capital exhausted"):
        enforce_capital_intake(portfolio_state, new_exposure, default_capital_config)


def test_ac004_broker_cash_independence(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Cash Independence (FINDING-005, FINDING-006).

    Even if broker account has $100,000 in unencumbered cash balance,
    Core policy ceiling remains strictly 80% ($80,000).
    A trade of $85,000 is rejected despite $100,000 cash availability.
    """
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,  # 100% cash
        positions=(),
        timestamp=sample_timestamp,
    )

    exposure = ProposedExposure(
        symbol="AMZN",
        additional_shares=850,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=4_250.0,
        effective_market_value=85_000.0,
    )

    assessment = evaluate_capital_intake(portfolio_state, exposure, default_capital_config)
    assert assessment.is_approved is False
    assert assessment.status == CapitalSleeveStatus.BLOCKED_RESERVE_ENCROACHMENT
    with pytest.raises(ReserveEncroachmentError):
        enforce_capital_intake(portfolio_state, exposure, default_capital_config)


def test_ac005_sleeve_filtering(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Isolation & Sleeve Identification (FINDING-002).

    Positions in non-Core sleeves (e.g. BLACK_SWAN) must NOT consume Core capacity.
    Portfolio equity: $100,000 (Core capacity = $80,000).
    Open positions:
    - Core position: $60,000.
    - Black Swan hedge: $4,000.
    Total deployed across all sleeves = $64,000.
    Core deployed must evaluate to exactly $60,000.
    Proposed trade of $15,000 gives post Core $75,000 <= $80,000 -> APPROVED.
    """
    core_pos = PositionRiskSnapshot(
        symbol="AAPL",
        shares=600,
        current_price=100.0,
        stop_loss_price=95.0,
        open_risk_dollars=3_000.0,
        market_value=60_000.0,
        sleeve=CapitalSleeve.CORE,
    )
    bs_pos = PositionRiskSnapshot(
        symbol="SPY_PUTS",
        shares=40,
        current_price=100.0,
        stop_loss_price=50.0,
        open_risk_dollars=2_000.0,
        market_value=4_000.0,
        sleeve=CapitalSleeve.BLACK_SWAN,
    )
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=36_000.0,
        positions=(core_pos, bs_pos),
        timestamp=sample_timestamp,
    )

    proposed = ProposedExposure(
        symbol="MSFT",
        additional_shares=150,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=750.0,
        effective_market_value=15_000.0,
    )

    assessment = evaluate_capital_intake(portfolio_state, proposed, default_capital_config)
    assert assessment.is_approved is True
    assert assessment.status == CapitalSleeveStatus.APPROVED
    assert assessment.current_core_deployed_dollars == 60_000.0
    assert assessment.post_core_deployed_dollars == 75_000.0
    assert assessment.available_core_dollars == 20_000.0


def test_ac006_target_sleeve_fail_closed(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Fail-Closed on Non-Core Target Sleeve (MISSING-002).

    Attempting general trading intake targeting RESERVE or BLACK_SWAN
    must fail closed with UnknownCapitalInputError.
    """
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        timestamp=sample_timestamp,
    )

    reserve_exposure = ProposedExposure(
        symbol="CASH_ASSET",
        additional_shares=100,
        entry_price=50.0,
        stop_loss_price=45.0,
        effective_risk_dollars=500.0,
        effective_market_value=5_000.0,
        target_sleeve=CapitalSleeve.RESERVE,
    )

    with pytest.raises(UnknownCapitalInputError, match="Only 'CORE' sleeve accepts trading intake"):
        evaluate_capital_intake(portfolio_state, reserve_exposure, default_capital_config)


def test_ac007_fail_closed_invalid_and_nan_inputs(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Fail-Closed Input Gating (INV-008)."""
    # Verify non-positive and infinite equity fails closed
    with pytest.raises(UnknownCapitalInputError):
        calculate_sleeve_allocations(-1.0, default_capital_config)

    with pytest.raises(UnknownCapitalInputError):
        calculate_sleeve_allocations(float("inf"), default_capital_config)


def test_ac008_determinism_and_immutability(
    default_capital_config: CapitalAllocationConfig,
    sample_timestamp: datetime,
) -> None:
    """Lens: Determinism & Audit Immutability (INV-001, INV-006, FINDING-007).

    Evaluator must produce byte-identical JSON receipts and echo timestamp.
    Assessment models must be frozen and immutable.
    """
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=50_000.0,
        positions=(),
        timestamp=sample_timestamp,
    )
    proposed = ProposedExposure(
        symbol="AAPL",
        additional_shares=500,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=2_500.0,
        effective_market_value=50_000.0,
    )

    res1 = evaluate_capital_intake(portfolio_state, proposed, default_capital_config)
    res2 = evaluate_capital_intake(portfolio_state, proposed, default_capital_config)

    # Identical objects and byte-identical JSON
    assert res1 == res2
    assert res1.model_dump_json() == res2.model_dump_json()
    assert res1.timestamp == sample_timestamp

    # Immutability
    with pytest.raises(ValidationError):
        res1.is_approved = False  # type: ignore[misc]
