"""Tests for Story GUARD-002: Concentration Intake Gating.

Verifies:
- FR-008: Concentration limits restrict new exposure without forcing liquidation of valid positions.
- Philosophy §2.6: Concentration controls new exposure (intake constraint only).
- Philosophy §2.2: Let winners run; no trimming or rebalancing exits on winning positions.
- INV-001: Deterministic, reproducible execution mathematics.
- INV-006: Pure audit assessment receipt generation without LLM heuristics.
- INV-008: Fail-closed on UNKNOWN, missing, NaN, or non-positive inputs.
- AC-001: Single-position initial entry intake boundaries (exact 20% vs 20.01% breach).
- AC-002: Pyramid addition intake gating.
- AC-003: Organic appreciation immunity and add prohibition.
- AC-004: Fail-closed UNKNOWN input handling & absent symbol handling (FINDING-004).
- AC-005: Pure deterministic audit receipts & echoed timestamps.
- AC-006: Deterministic exception dispatch via typed status (FINDING-003).
- Cross-module integration with toso.continuation (FINDING-001, MISSING-004).
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import RiskConfig
from toso.continuation.errors import ForcedConcentrationExitProhibitedError
from toso.continuation.evaluator import (
    evaluate_winner_continuation,
    validate_exit_request,
)
from toso.continuation.models import ExitType
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.exit.models import CorePosition, PositionStatus
from toso.guardian import (
    ConcentrationIntakeBlockedError,
    ConcentrationIntakeStatus,
    OrganicConcentrationAddProhibitedError,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProposedExposure,
    UnknownConcentrationInputError,
    calculate_position_concentration,
    enforce_concentration_intake,
    evaluate_concentration_intake,
)


@pytest.fixture
def risk_config() -> RiskConfig:
    """Standard approved risk configuration with 20% concentration cap."""
    return RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,  # 20% max single-stock intake limit
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def baseline_timestamp() -> datetime:
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clean_portfolio_state(baseline_timestamp: datetime) -> PortfolioStateSnapshot:
    """Clean portfolio state with $100,000 equity and no open positions."""
    return PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=100000.0,
        positions=(),
        timestamp=baseline_timestamp,
    )


def test_ac001_single_position_initial_entry_boundaries(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
) -> None:
    """AC-001 & FINDING-005: Initial entry exact 20% approval and 20.001% rejection."""
    # 1. Exact boundary: Proposed initial entry of exactly $20,000 (20.0% of $100k) -> APPROVED
    proposed_exact = ProposedExposure(
        symbol="AAPL",
        is_pyramid=False,
        additional_shares=200,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=1000.0,
        effective_market_value=20000.0,
    )
    assessment = evaluate_concentration_intake(proposed_exact, clean_portfolio_state, risk_config)
    assert assessment.is_approved is True
    assert assessment.status == ConcentrationIntakeStatus.APPROVED
    assert assessment.post_concentration_pct == 0.20
    assert assessment.current_position_market_value == 0.0
    assert assessment.is_currently_breached is False
    assert assessment.rejection_reason is None

    # 2. Breach boundary: Proposed initial entry of $20,001 (20.001% of $100k) -> REJECTED
    proposed_breach = ProposedExposure(
        symbol="AAPL",
        is_pyramid=False,
        additional_shares=200,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=1000.0,
        effective_market_value=20001.0,
    )
    assessment_breach = evaluate_concentration_intake(
        proposed_breach, clean_portfolio_state, risk_config
    )
    assert assessment_breach.is_approved is False
    assert assessment_breach.status == (
        ConcentrationIntakeStatus.BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED
    )
    assert "exceeding maximum concentration limit of 20.00%" in assessment_breach.rejection_reason

    with pytest.raises(ConcentrationIntakeBlockedError):
        enforce_concentration_intake(proposed_breach, clean_portfolio_state, risk_config)


def test_ac002_pyramid_addition_intake_gating(
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-002: Pyramid addition evaluated against resulting aggregate position weight."""
    # Existing NVDA position of $12,000 (12.0% of $100k)
    existing_nvda = PositionRiskSnapshot(
        symbol="NVDA",
        shares=120,
        current_price=100.0,
        stop_loss_price=95.0,
        open_risk_dollars=600.0,
        market_value=12000.0,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=88000.0,
        positions=(existing_nvda,),
        timestamp=baseline_timestamp,
    )

    # 1. Addition of $8,000 brings total NVDA position to $20,000 (exactly 20.0%) -> APPROVED
    prop_add_exact = ProposedExposure(
        symbol="NVDA",
        is_pyramid=True,
        additional_shares=80,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=400.0,
        effective_market_value=8000.0,
    )
    ass_add_exact = evaluate_concentration_intake(prop_add_exact, state, risk_config)
    assert ass_add_exact.is_approved is True
    assert ass_add_exact.status == ConcentrationIntakeStatus.APPROVED
    assert ass_add_exact.post_position_market_value == 20000.0
    assert ass_add_exact.post_concentration_pct == 0.20

    # 2. Addition of $8,010 brings total NVDA position to $20,010 (20.01%) -> REJECTED
    prop_add_breach = ProposedExposure(
        symbol="NVDA",
        is_pyramid=True,
        additional_shares=80,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=400.0,
        effective_market_value=8010.0,
    )
    ass_add_breach = evaluate_concentration_intake(prop_add_breach, state, risk_config)
    assert ass_add_breach.is_approved is False
    assert ass_add_breach.status == ConcentrationIntakeStatus.BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED
    assert "exceeding maximum concentration limit of 20.00%" in ass_add_breach.rejection_reason

    with pytest.raises(ConcentrationIntakeBlockedError):
        enforce_concentration_intake(prop_add_breach, state, risk_config)


def test_ac003_organic_appreciation_immunity_and_add_prohibition(
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-003, FINDING-001, MISSING-004: Organic breach immunity & intake prohibition."""
    # 1. Existing TSLA position originally entered small, but appreciated to $25,000 (25.0% > 20%)
    tsla_snapshot = PositionRiskSnapshot(
        symbol="TSLA",
        shares=100,
        current_price=250.0,
        stop_loss_price=200.0,
        open_risk_dollars=5000.0,
        market_value=25000.0,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=75000.0,
        positions=(tsla_snapshot,),
        timestamp=baseline_timestamp,
    )

    # 2. Integration with continuation evaluator: position MUST remain CONTINUE_HOLD (FR-008)
    entry_order = CoreEntryOrder(
        order_id="ENTRY-TSLA-001",
        symbol="TSLA",
        timestamp=baseline_timestamp,
        entry_price=150.0,
        invalidation=InvalidationDefinition(
            price=120.0,
            invalidation_type="ATR_STOP",
            atr_multiple=2.0,
            reference_atr=15.0,
            established_at=baseline_timestamp,
            reason="Initial 2.0x ATR stop",
        ),
        risk_accounting=GapSlippageRiskModel.compute(
            entry_price=150.0,
            invalidation_price=120.0,
            slippage_buffer_pct=0.005,
        ),
        reason="Breakout signal entry",
    )
    core_pos = CorePosition(
        position_id="POS-TSLA-001",
        symbol="TSLA",
        entry_order=entry_order,
        quantity=100,
        entry_price=150.0,
        invalidation=InvalidationDefinition(
            price=200.0,
            invalidation_type="TRAILING_STOP",
            atr_multiple=2.0,
            reference_atr=15.0,
            established_at=baseline_timestamp,
            reason="Trailing stop ratchet",
        ),
        status=PositionStatus.OPEN,
        opened_at=baseline_timestamp,
    )

    continuation_decision = evaluate_winner_continuation(
        position=core_pos,
        current_price=250.0,
        timestamp=baseline_timestamp,
        portfolio_equity=100000.0,
        risk_config=risk_config,
    )
    assert continuation_decision.action == "CONTINUE_HOLD"
    assert continuation_decision.is_concentration_breached is True
    assert continuation_decision.is_continuation_permitted is True
    assert "Organic weight (25.0%) exceeds intake cap" in continuation_decision.explanation

    # Attempting to liquidate or trim on concentration grounds fails closed
    with pytest.raises(ForcedConcentrationExitProhibitedError):
        validate_exit_request(
            position=core_pos,
            proposed_exit_type=ExitType.CONCENTRATION_REBALANCE,
            requester_id="guardian_risk_monitor",
            timestamp=baseline_timestamp,
        )

    # 3. But attempting to add ANY new shares to TSLA is strictly blocked
    prop_tsla_add = ProposedExposure(
        symbol="TSLA",
        is_pyramid=True,
        additional_shares=10,
        entry_price=250.0,
        stop_loss_price=220.0,
        effective_risk_dollars=300.0,
        effective_market_value=2500.0,
    )
    ass_tsla_add = evaluate_concentration_intake(prop_tsla_add, state, risk_config)
    assert ass_tsla_add.is_approved is False
    assert ass_tsla_add.status == ConcentrationIntakeStatus.BLOCKED_ORGANIC_BREACH
    assert ass_tsla_add.is_currently_breached is True
    assert "organically breached concentration cap" in ass_tsla_add.rejection_reason

    with pytest.raises(OrganicConcentrationAddProhibitedError):
        enforce_concentration_intake(prop_tsla_add, state, risk_config)

    # 4. Unrelated valid symbol (e.g. MSFT at 0%) remains eligible for intake up to 20%
    prop_msft_entry = ProposedExposure(
        symbol="MSFT",
        is_pyramid=False,
        additional_shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        effective_risk_dollars=1000.0,
        effective_market_value=15000.0,  # 15.0% < 20.0%
    )
    ass_msft = evaluate_concentration_intake(prop_msft_entry, state, risk_config)
    assert ass_msft.is_approved is True
    assert ass_msft.status == ConcentrationIntakeStatus.APPROVED


def test_ac004_fail_closed_unknown_inputs_and_absent_symbols(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-004, FINDING-004, MISSING-003, INV-008: Fail-closed handling & absent symbols."""
    # 1. Absent symbol cleanly evaluated as 0.0 current exposure (FINDING-004)
    prop_new = ProposedExposure(
        symbol="GOOGL",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=90.0,
        effective_risk_dollars=500.0,
        effective_market_value=5000.0,
    )
    ass_new = evaluate_concentration_intake(prop_new, clean_portfolio_state, risk_config)
    assert ass_new.current_position_market_value == 0.0
    assert ass_new.current_concentration_pct == 0.0
    assert ass_new.is_approved is True

    # 2. Non-positive or NaN equity fails closed
    with pytest.raises(UnknownConcentrationInputError, match="must be strictly positive"):
        calculate_position_concentration(1000.0, 0.0)

    with pytest.raises(UnknownConcentrationInputError, match="NaN or infinite"):
        calculate_position_concentration(1000.0, float("nan"))

    # 3. Invalid config (max_position_concentration_pct > 1.0 or <= 0.0)
    invalid_config = RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )
    # Monkeypatch for test of evaluator defense against corrupted runtime config
    object.__setattr__(invalid_config, "max_position_concentration_pct", 1.5)
    with pytest.raises(UnknownConcentrationInputError, match="cannot exceed 1.0"):
        evaluate_concentration_intake(prop_new, clean_portfolio_state, invalid_config)


def test_ac005_pure_determinism_and_timestamp_echo(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-005, INV-001, INV-006: Pure calculation and echoed snapshot timestamp."""
    prop = ProposedExposure(
        symbol="AMZN",
        is_pyramid=False,
        additional_shares=100,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=500.0,
        effective_market_value=10000.0,
    )

    receipt1 = evaluate_concentration_intake(prop, clean_portfolio_state, risk_config)
    receipt2 = evaluate_concentration_intake(prop, clean_portfolio_state, risk_config)

    assert receipt1 == receipt2
    assert receipt1.timestamp == baseline_timestamp
    assert receipt2.timestamp == baseline_timestamp
    assert receipt1.model_dump_json() == receipt2.model_dump_json()


def test_finding002_and_005_numerical_tolerance_boundary(
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """FINDING-002: Position at 19.999999% is not mistakenly treated as an organic breach."""
    # Position with $19,999.99 market value (19.99999% of $100k)
    near_cap_pos = PositionRiskSnapshot(
        symbol="AAPL",
        shares=100,
        current_price=199.9999,
        stop_loss_price=190.0,
        open_risk_dollars=999.99,
        market_value=19999.99,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=80000.0,
        positions=(near_cap_pos,),
        timestamp=baseline_timestamp,
    )

    # Proposed tiny addition of $1.00 -> total $20,000.99 (20.00099% > 20.0%)
    # Should be rejected for EXCEEDING INTAKE CAPACITY, not as an existing organic breach!
    prop = ProposedExposure(
        symbol="AAPL",
        is_pyramid=True,
        additional_shares=1,
        entry_price=1.0,
        stop_loss_price=0.5,
        effective_risk_dollars=0.5,
        effective_market_value=1.0,
    )
    assessment = evaluate_concentration_intake(prop, state, risk_config)
    assert assessment.is_approved is False
    assert assessment.is_currently_breached is False  # NOT an organic breach
    assert assessment.status == ConcentrationIntakeStatus.BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED
