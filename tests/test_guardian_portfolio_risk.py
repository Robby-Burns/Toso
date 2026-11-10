"""Tests for Story GUARD-001: Portfolio Risk Controls.

Verifies:
- FR-002: Deterministic risk and sizing controls.
- FR-008: Intake constraint only (no forced liquidation of winners).
- FR-009: Portfolio risk controls (portfolio heat, per-trade, aggregate risk,
  drawdown, gross exposure).
- INV-006: Zero LLM interpretation at execution time.
- INV-008: Fail-closed on UNKNOWN, missing, NaN, or non-positive inputs.
- AC-001: Total portfolio open risk (heat) ceiling gating.
- AC-002: Single-trade risk and aggregate position risk ceilings.
- AC-003: Emergency peak-to-trough drawdown halt circuit breaker.
- AC-004: Gross market exposure & leverage capacity limits.
- AC-005: Fail-closed UNKNOWN input handling.
- AC-006: Pure deterministic audit receipts.
- FINDING-001: Equivalence between evaluate_portfolio_risk and enforce_portfolio_risk_intake.
- FINDING-002: Multi-violation capture in rejection_reasons.
- FINDING-003: Exact boundary operator semantics (=, <, >).
- FINDING-005: Deterministic timestamp echo (no wall-clock reads).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toso.config.models import CapitalAllocationConfig, RiskConfig
from toso.guardian import (
    AggregatePositionRiskExceededError,
    GrossExposureLimitExceededError,
    PortfolioDrawdownHaltError,
    PortfolioHeatExceededError,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProposedExposure,
    RiskStatus,
    SingleTradeRiskExceededError,
    UnknownPortfolioRiskInputError,
    calculate_portfolio_drawdown,
    enforce_portfolio_risk_intake,
    evaluate_portfolio_risk,
)


@pytest.fixture
def risk_config() -> RiskConfig:
    """Standard approved risk configuration."""
    return RiskConfig(
        max_risk_per_trade_pct=0.01,  # 1% per trade ($1,000 on $100k)
        max_aggregate_position_risk_pct=0.02,  # 2% per symbol ($2,000 on $100k)
        max_portfolio_risk_pct=0.06,  # 6% portfolio heat ($6,000 on $100k)
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,  # 10% drawdown halt
    )


@pytest.fixture
def capital_config() -> CapitalAllocationConfig:
    """Standard approved capital allocation configuration."""
    return CapitalAllocationConfig(
        core_allocation_pct=0.80,  # 80% Core Trader
        reserve_allocation_pct=0.15,
        black_swan_allocation_pct=0.05,
    )


@pytest.fixture
def baseline_timestamp() -> datetime:
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clean_portfolio_state(baseline_timestamp: datetime) -> PortfolioStateSnapshot:
    """Portfolio state with $100,000 equity, no drawdown, and no open positions."""
    return PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=100000.0,
        positions=(),
        timestamp=baseline_timestamp,
    )


def test_ac001_portfolio_heat_boundary_and_rejection(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-001 & FINDING-003: Verify total portfolio heat gating and exact boundary semantics."""
    # Existing positions totaling 5.0% open risk ($5,000)
    pos1 = PositionRiskSnapshot(
        symbol="AAPL",
        shares=100,
        current_price=150.0,
        stop_loss_price=125.0,
        open_risk_dollars=2500.0,
        market_value=15000.0,
    )
    pos2 = PositionRiskSnapshot(
        symbol="MSFT",
        shares=100,
        current_price=250.0,
        stop_loss_price=225.0,
        open_risk_dollars=2500.0,
        market_value=25000.0,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=60000.0,
        positions=(pos1, pos2),
        timestamp=baseline_timestamp,
    )

    # 1. Exact boundary: Proposed $1,000 addition brings heat to exactly 6.0% -> APPROVED
    proposed_exact = ProposedExposure(
        symbol="NVDA",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=80.0,
        effective_risk_dollars=1000.0,
        effective_market_value=5000.0,
    )
    assessment = evaluate_portfolio_risk(proposed_exact, state, risk_config)
    assert assessment.is_approved is True
    assert assessment.post_heat_pct == 0.06
    assert assessment.rejection_reason is None

    # 2. Breach boundary: Proposed addition of $1,000 when existing heat is $5,001 (5.001%)
    # brings heat to $6,001 (6.001%). Single trade risk is $1,000 (1.0%), so not breached.
    pos1_breach = PositionRiskSnapshot(
        symbol="AAPL",
        shares=100,
        current_price=150.0,
        stop_loss_price=125.0,
        open_risk_dollars=2501.0,
        market_value=15000.0,
    )
    state_breach = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=60000.0,
        positions=(pos1_breach, pos2),
        timestamp=baseline_timestamp,
    )
    assessment_breach = evaluate_portfolio_risk(proposed_exact, state_breach, risk_config)
    assert assessment_breach.is_approved is False
    assert assessment_breach.status == RiskStatus.BREACH_BLOCKED
    assert "exceeds maximum allowed 6.00%" in assessment_breach.rejection_reason

    # Enforce wrapper raises PortfolioHeatExceededError
    with pytest.raises(PortfolioHeatExceededError, match="exceeds maximum allowed 6.00%"):
        enforce_portfolio_risk_intake(proposed_exact, state_breach, risk_config)


def test_ac002_single_trade_and_aggregate_position_risk_boundaries(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-002: Verify per-trade risk (1%) and aggregate per-symbol risk (2%) ceilings."""
    # 1. Single-trade risk exact at 1.0% ($1,000) -> APPROVED
    prop_trade_exact = ProposedExposure(
        symbol="GOOGL",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=80.0,
        effective_risk_dollars=1000.0,
        effective_market_value=5000.0,
    )
    ass_trade_exact = evaluate_portfolio_risk(prop_trade_exact, clean_portfolio_state, risk_config)
    assert ass_trade_exact.is_approved is True

    # 2. Single-trade risk breach at $1,001 (1.001%) -> REJECTED
    prop_trade_breach = ProposedExposure(
        symbol="GOOGL",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=79.98,
        effective_risk_dollars=1001.0,
        effective_market_value=5000.0,
    )
    ass_trade_breach = evaluate_portfolio_risk(
        prop_trade_breach, clean_portfolio_state, risk_config
    )
    assert ass_trade_breach.is_approved is False
    assert "Proposed single-trade risk 1.00% exceeds maximum allowed 1.00%" in (
        ass_trade_breach.rejection_reason
    )

    with pytest.raises(SingleTradeRiskExceededError):
        enforce_portfolio_risk_intake(prop_trade_breach, clean_portfolio_state, risk_config)

    # 3. Aggregate position risk per symbol: Existing $1,200 risk on AMZN.
    # Proposed pyramid add of $800 brings aggregate AMZN risk to exactly $2,000 (2.0%) -> APPROVED
    amzn_pos = PositionRiskSnapshot(
        symbol="AMZN",
        shares=100,
        current_price=150.0,
        stop_loss_price=138.0,
        open_risk_dollars=1200.0,
        market_value=15000.0,
    )
    amzn_state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=85000.0,
        positions=(amzn_pos,),
        timestamp=baseline_timestamp,
    )

    prop_amzn_exact = ProposedExposure(
        symbol="AMZN",
        is_pyramid=True,
        additional_shares=40,
        entry_price=150.0,
        stop_loss_price=130.0,
        effective_risk_dollars=800.0,  # <= 1.0% single trade risk
        effective_market_value=6000.0,
    )
    ass_amzn_exact = evaluate_portfolio_risk(prop_amzn_exact, amzn_state, risk_config)
    assert ass_amzn_exact.is_approved is True
    assert ass_amzn_exact.proposed_symbol_risk_pct == 0.02

    # Proposed pyramid add of $850 brings aggregate AMZN risk to $2,050 (2.05% > 2.0%) -> REJECTED
    prop_amzn_breach = ProposedExposure(
        symbol="AMZN",
        is_pyramid=True,
        additional_shares=40,
        entry_price=150.0,
        stop_loss_price=128.75,
        effective_risk_dollars=850.0,
        effective_market_value=6000.0,
    )
    ass_amzn_breach = evaluate_portfolio_risk(prop_amzn_breach, amzn_state, risk_config)
    assert ass_amzn_breach.is_approved is False
    assert "Aggregate position risk for AMZN 2.05% exceeds maximum allowed 2.00%" in (
        ass_amzn_breach.rejection_reason
    )

    with pytest.raises(AggregatePositionRiskExceededError):
        enforce_portfolio_risk_intake(prop_amzn_breach, amzn_state, risk_config)


def test_ac003_emergency_drawdown_stop_halt(
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-003: Verify peak-to-trough drawdown >= emergency_drawdown_stop_pct triggers HALT."""
    # Peak equity: $100,000.
    # 1. Exactly 10% drawdown ($90,000 equity) reaches threshold -> HALTED
    state_halt_exact = PortfolioStateSnapshot(
        portfolio_equity=90000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=90000.0,
        positions=(),
        timestamp=baseline_timestamp,
    )
    proposed = ProposedExposure(
        symbol="AAPL",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=250.0,
        effective_market_value=5000.0,
    )
    assessment = evaluate_portfolio_risk(proposed, state_halt_exact, risk_config)
    assert assessment.is_approved is False
    assert assessment.status == RiskStatus.HALTED
    assert assessment.current_drawdown_pct == 0.10
    assert "reaches or breaches emergency stop threshold 10.00%" in assessment.rejection_reason

    with pytest.raises(PortfolioDrawdownHaltError, match="reaches or breaches"):
        enforce_portfolio_risk_intake(proposed, state_halt_exact, risk_config)

    # 2. Drawdown below threshold (9.9% = $90,100 equity) -> APPROVED (not halted)
    state_sub_halt = PortfolioStateSnapshot(
        portfolio_equity=90100.0,
        peak_portfolio_equity=100000.0,
        cash_balance=90100.0,
        positions=(),
        timestamp=baseline_timestamp,
    )
    ass_sub = evaluate_portfolio_risk(proposed, state_sub_halt, risk_config)
    assert ass_sub.is_approved is True
    assert ass_sub.status != RiskStatus.HALTED


def test_ac004_gross_exposure_and_capital_capacity_limits(
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-004: Verify gross market exposure capacity limits (Core 80% capital allocation)."""
    # Existing positions totaling $70,000 market value
    pos = PositionRiskSnapshot(
        symbol="AAPL",
        shares=700,
        current_price=100.0,
        stop_loss_price=98.0,
        open_risk_dollars=1400.0,
        market_value=70000.0,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=30000.0,
        positions=(pos,),
        timestamp=baseline_timestamp,
    )

    # Core capacity is 80% = $80,000.
    # 1. Proposed market value of $10,000 brings total gross to exactly $80,000 -> APPROVED
    proposed_exact = ProposedExposure(
        symbol="MSFT",
        is_pyramid=False,
        additional_shares=100,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=500.0,
        effective_market_value=10000.0,
    )
    ass_exact = evaluate_portfolio_risk(
        proposed_exact, state, risk_config, capital_config=capital_config
    )
    assert ass_exact.is_approved is True
    assert ass_exact.post_gross_exposure_dollars == 80000.0

    # 2. Proposed market value of $10,500 brings total gross to $80,500 (> $80,000) -> REJECTED
    proposed_breach = ProposedExposure(
        symbol="MSFT",
        is_pyramid=False,
        additional_shares=105,
        entry_price=100.0,
        stop_loss_price=95.0,
        effective_risk_dollars=525.0,
        effective_market_value=10500.0,
    )
    ass_breach = evaluate_portfolio_risk(
        proposed_breach, state, risk_config, capital_config=capital_config
    )
    assert ass_breach.is_approved is False
    assert "exceeds gross capacity $80000.00" in ass_breach.rejection_reason

    with pytest.raises(GrossExposureLimitExceededError):
        enforce_portfolio_risk_intake(
            proposed_breach, state, risk_config, capital_config=capital_config
        )


def test_ac005_fail_closed_unknown_and_invalid_inputs(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-005 & INV-008: Fail-closed on UNKNOWN, NaN, infinite, or non-positive inputs."""
    # 1. Non-positive equity
    with pytest.raises(UnknownPortfolioRiskInputError, match="must be strictly positive"):
        calculate_portfolio_drawdown(0.0, 100000.0)

    with pytest.raises(UnknownPortfolioRiskInputError, match="must be strictly positive"):
        calculate_portfolio_drawdown(100000.0, -500.0)

    # 2. NaN or infinite equity
    with pytest.raises(UnknownPortfolioRiskInputError, match="NaN or infinite"):
        calculate_portfolio_drawdown(float("nan"), 100000.0)

    with pytest.raises(UnknownPortfolioRiskInputError, match="NaN or infinite"):
        calculate_portfolio_drawdown(100000.0, float("inf"))

    # 3. Model level validation: ProposedExposure additional_shares <= 0 (MISSING-001)
    with pytest.raises(ValidationError):
        ProposedExposure(
            symbol="AAPL",
            is_pyramid=False,
            additional_shares=0,  # Invalid: must be gt=0
            entry_price=100.0,
            stop_loss_price=95.0,
            effective_risk_dollars=500.0,
            effective_market_value=5000.0,
        )

    # 4. Model level validation: Non-positive equity in PortfolioStateSnapshot
    with pytest.raises(ValidationError):
        PortfolioStateSnapshot(
            portfolio_equity=-1000.0,
            peak_portfolio_equity=100000.0,
            cash_balance=0.0,
            positions=(),
            timestamp=baseline_timestamp,
        )


def test_ac006_pure_determinism_and_timestamp_echo(
    clean_portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    baseline_timestamp: datetime,
) -> None:
    """AC-006, FINDING-005, INV-001 & INV-006: Pure determinism and echoed timestamp."""
    proposed = ProposedExposure(
        symbol="AAPL",
        is_pyramid=False,
        additional_shares=50,
        entry_price=100.0,
        stop_loss_price=90.0,
        effective_risk_dollars=500.0,
        effective_market_value=5000.0,
    )

    receipt1 = evaluate_portfolio_risk(proposed, clean_portfolio_state, risk_config)
    receipt2 = evaluate_portfolio_risk(proposed, clean_portfolio_state, risk_config)

    # Byte-identical receipts without any wall-clock variation
    assert receipt1 == receipt2
    assert receipt1.timestamp == baseline_timestamp
    assert receipt2.timestamp == baseline_timestamp
    assert receipt1.model_dump_json() == receipt2.model_dump_json()


def test_finding001_and_002_multi_violation_reporting_and_precedence(
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    baseline_timestamp: datetime,
) -> None:
    """FINDING-001 & FINDING-002: Verify multiple simultaneous violations are all captured."""
    # Portfolio in 12% drawdown ($88,000 equity on $100k peak)
    # Also proposed trade exceeds single-trade risk, aggregate symbol risk, heat, and gross capacity
    existing_pos = PositionRiskSnapshot(
        symbol="TSLA",
        shares=700,
        current_price=100.0,
        stop_loss_price=95.0,
        open_risk_dollars=3500.0,
        market_value=70000.0,
    )
    state = PortfolioStateSnapshot(
        portfolio_equity=88000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=18000.0,
        positions=(existing_pos,),
        timestamp=baseline_timestamp,
    )

    # Proposed trade violates:
    # 1. Drawdown halt (current is 12% >= 10%)
    # 2. Single-trade risk ($2,000 > 1% of $88,000 = $880)
    # 3. Aggregate position risk ($3,500 + $2,000 = $5,500 > 2% of $88k = $1,760)
    # 4. Total portfolio heat ($3,500 + $2,000 = $5,500 > 6% of $88k = $5,280)
    # 5. Gross exposure ($70,000 + $20,000 = $90,000 > 80% Core of $88k = $70,400)
    proposed = ProposedExposure(
        symbol="TSLA",
        is_pyramid=True,
        additional_shares=200,
        entry_price=100.0,
        stop_loss_price=90.0,
        effective_risk_dollars=2000.0,
        effective_market_value=20000.0,
    )

    assessment = evaluate_portfolio_risk(
        proposed, state, risk_config, capital_config=capital_config
    )

    assert assessment.is_approved is False
    assert assessment.status == RiskStatus.HALTED
    # All 5 violations are captured in rejection_reasons
    assert len(assessment.rejection_reasons) == 5
    assert "reaches or breaches emergency stop threshold" in assessment.rejection_reasons[0]
    assert "Proposed single-trade risk" in assessment.rejection_reasons[1]
    assert "Aggregate position risk for TSLA" in assessment.rejection_reasons[2]
    assert "Projected portfolio heat" in assessment.rejection_reasons[3]
    assert "Projected gross exposure" in assessment.rejection_reasons[4]

    # Highest precedence violation is surfaced in primary rejection_reason
    assert assessment.rejection_reason == assessment.rejection_reasons[0]

    # Enforce wrapper raises the highest precedence error
    with pytest.raises(PortfolioDrawdownHaltError):
        enforce_portfolio_risk_intake(proposed, state, risk_config, capital_config=capital_config)
