"""Tests for Story TRADER-005: Position Sizing & Gap/Slippage Accounting.

Verifies:
- FR-002: Deterministic position sizing based on risk budget and effective risk per share.
- FR-003: Explicit slippage buffer in denominator; adverse gap stress scenarios modeled.
- AC-004 & INV-006: Pure mathematical determinism, concentration capping, portfolio risk
  ceiling enforcement, and fail-closed safety.
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import CapitalAllocationConfig, RiskConfig, StrategyConfig
from toso.entry.evaluator import create_entry_from_signal
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.exit.models import CorePosition, PositionStatus
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.models import Bar, BarSeries
from toso.sizing import (
    ConcentrationLimitExceededError,
    InsufficientCapitalError,
    InvalidSizingInputError,
    PortfolioRiskLimitExceededError,
    PositionSizingDecision,
    SizingConstraint,
    calculate_position_size,
    verify_concentration_limit,
)


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,  # 1%
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,  # 6%
        max_position_concentration_pct=0.20,  # 20%
        slippage_buffer_pct=0.005,  # 0.5% buffer
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def capital_config() -> CapitalAllocationConfig:
    return CapitalAllocationConfig(
        core_allocation_pct=0.80,  # 80% Core
        reserve_allocation_pct=0.15,  # 15% Reserve
        black_swan_allocation_pct=0.05,  # 5% Black Swan
    )


@pytest.fixture
def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="CORE-TREND-TEST",
        timeframe="1d",
        fast_ma_period=5,
        slow_ma_period=10,
        atr_period=5,
        breakout_lookback_bars=5,
        invalidation_atr_multiple=2.0,
        pyramid_min_profit_r=1.0,
        max_pyramid_adds=3,
    )


@pytest.fixture
def sample_entry_order() -> CoreEntryOrder:
    # Entry: $100.00, Stop: $95.00 -> Nominal risk = $5.00
    # Slippage buffer: 0.005 * $100 = $0.50
    # Effective risk per share = $5.50
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    invalidation = InvalidationDefinition(
        price=95.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=False,
        reason="Entry stop at 95.00",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0,
        invalidation_price=95.0,
        slippage_buffer_pct=0.005,
    )
    return CoreEntryOrder(
        order_id="ORD-TEST-001",
        symbol="AAPL",
        timestamp=now,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_accounting,
        reason="Test breakout entry",
    )


# ============================================================================
# 1. Deterministic Risk-Budget Sizing & Slippage Buffer (FR-002, FR-003)
# ============================================================================


def test_deterministic_risk_budget_sizing(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """FR-002: Position size is determined deterministically from risk budget and effective risk."""
    portfolio_equity = 100_000.0
    # Risk budget = 100_000 * 0.01 = $1,000.00
    # Effective risk per share = ($100 - $95) + ($100 * 0.005) = $5.00 + $0.50 = $5.50
    # Sizing = floor(1000 / 5.50) = floor(181.818...) = 181 shares
    decision = calculate_position_size(
        entry_order=sample_entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
    )

    assert isinstance(decision, PositionSizingDecision)
    assert decision.allocated_shares == 181
    assert decision.binding_constraint == SizingConstraint.RISK_BUDGET
    assert decision.entry_price == 100.0
    assert decision.invalidation_price == 95.0
    assert decision.nominal_risk_per_share == 5.0
    assert decision.effective_risk_per_share == 5.50
    assert decision.market_value == 181 * 100.0
    assert decision.effective_risk_dollars == round(181 * 5.50, 4)
    assert decision.effective_risk_dollars <= decision.risk_budget_dollars
    assert decision.concentration_pct == round(18_100.0 / 100_000.0, 6)


def test_slippage_buffer_dampens_position_size(
    sample_entry_order: CoreEntryOrder,
    capital_config: CapitalAllocationConfig,
) -> None:
    """FR-003: Sizing denominator with slippage buffer results in fewer shares than nominal stop."""
    portfolio_equity = 100_000.0
    # With 0.5% slippage buffer (effective risk = $5.50) -> 181 shares
    cfg_with_buffer = RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )
    decision_with_buffer = calculate_position_size(
        entry_order=sample_entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=cfg_with_buffer,
        capital_config=capital_config,
    )

    # With 0% slippage buffer (effective risk = $5.00) -> floor(1000 / 5.00) = 200 shares
    cfg_zero_buffer = RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.0,
        emergency_drawdown_stop_pct=0.10,
    )
    zero_buffer_risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0,
        invalidation_price=95.0,
        slippage_buffer_pct=0.0,
    )
    zero_buffer_order = CoreEntryOrder(
        order_id="ORD-ZERO-BUF",
        symbol="AAPL",
        timestamp=sample_entry_order.timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=sample_entry_order.invalidation,
        risk_accounting=zero_buffer_risk_accounting,
        reason="Zero buffer test",
    )
    decision_zero_buffer = calculate_position_size(
        entry_order=zero_buffer_order,
        portfolio_equity=portfolio_equity,
        risk_config=cfg_zero_buffer,
        capital_config=capital_config,
    )

    assert decision_with_buffer.allocated_shares == 181
    assert decision_zero_buffer.allocated_shares == 200
    assert decision_with_buffer.allocated_shares < decision_zero_buffer.allocated_shares


# ============================================================================
# 2. Intake Concentration Gating & Hard Enforcement
# ============================================================================


def test_concentration_limit_caps_shares(
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Concentration cap bounds share allocation when stop distance is extremely tight."""
    portfolio_equity = 100_000.0
    # Stop is very tight: entry = $100.00, stop = $99.50
    # Nominal risk = $0.50, slippage buffer = $0.50 -> effective risk = $1.00
    # Risk budget = $1,000 -> risk_shares = 1,000 shares ($100k market value = 100%!)
    # But max concentration = 20% -> $20,000 / $100 = 200 shares!
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    invalidation = InvalidationDefinition(
        price=99.50,
        invalidation_type="ATR_STOP",
        atr_multiple=0.5,
        reference_atr=1.0,
        established_at=now,
        is_ratcheted=False,
        reason="Tight stop",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0,
        invalidation_price=99.50,
        slippage_buffer_pct=0.005,
    )
    tight_stop_order = CoreEntryOrder(
        order_id="ORD-TIGHT",
        symbol="MSFT",
        timestamp=now,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_accounting,
        reason="Tight stop test",
    )

    decision = calculate_position_size(
        entry_order=tight_stop_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
    )

    assert decision.allocated_shares == 200
    assert decision.binding_constraint == SizingConstraint.CONCENTRATION_LIMIT
    assert decision.market_value == 20_000.0
    assert decision.concentration_pct == 0.20


def test_verify_concentration_limit_enforcement(risk_config: RiskConfig) -> None:
    """verify_concentration_limit raises ConcentrationLimitExceededError when cap is breached."""
    portfolio_equity = 100_000.0
    # Max allowed concentration = 20% = $20,000
    verify_concentration_limit(
        market_value=20_000.0,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
    )

    with pytest.raises(ConcentrationLimitExceededError) as exc_info:
        verify_concentration_limit(
            market_value=20_001.0,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
        )
    assert "exceeds maximum concentration limit" in str(exc_info.value)


# ============================================================================
# 3. Core Capital Sleeve Constraints
# ============================================================================


def test_core_capital_allocation_constraint(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Sizing respects remaining Core capital allocation."""
    portfolio_equity = 100_000.0
    # Max Core allocation = 80% = $80,000
    # Current deployed = $75,000 -> Available Core = $5,000
    # Capital shares = floor(5000 / 100) = 50 shares
    # Risk shares would be 181, concentration shares would be 200
    decision = calculate_position_size(
        entry_order=sample_entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
        current_deployed_core_dollars=75_000.0,
    )

    assert decision.allocated_shares == 50
    assert decision.binding_constraint == SizingConstraint.CORE_CAPITAL_LIMIT
    assert decision.market_value == 5_000.0


def test_exhausted_core_capital_raises_insufficient_capital_error(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Fail closed when Core capital sleeve is fully exhausted."""
    portfolio_equity = 100_000.0
    # Max Core allocation = $80,000. Current deployed = $80,000.
    with pytest.raises(InsufficientCapitalError) as exc_info:
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
            capital_config=capital_config,
            current_deployed_core_dollars=80_000.0,
        )
    assert "Core capital allocation exhausted" in str(exc_info.value)


# ============================================================================
# 4. Portfolio Risk Ceiling Constraints (INV-006)
# ============================================================================


def test_portfolio_risk_ceiling_constrains_trade_budget(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Portfolio risk ceiling reduces single trade budget when available risk headroom is small."""
    portfolio_equity = 100_000.0
    # Max portfolio risk = 6% = $6,000
    # Current open risk = $5,500 -> Available risk = $500 (less than standard 1% / $1,000)
    # Sizing = floor(500 / 5.50) = floor(90.9) = 90 shares
    decision = calculate_position_size(
        entry_order=sample_entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
        current_open_risk_dollars=5_500.0,
    )

    assert decision.allocated_shares == 90
    assert decision.risk_budget_dollars == 500.0
    assert decision.binding_constraint == SizingConstraint.RISK_BUDGET
    assert decision.effective_risk_dollars <= 500.0


def test_portfolio_risk_ceiling_breach_raises_portfolio_risk_limit_exceeded_error(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Fail closed when portfolio open risk is at or above the maximum ceiling."""
    portfolio_equity = 100_000.0
    # Max portfolio risk = 6% = $6,000. Current open risk = $6,000.
    with pytest.raises(PortfolioRiskLimitExceededError) as exc_info:
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
            capital_config=capital_config,
            current_open_risk_dollars=6_000.0,
        )
    assert "Portfolio risk limit reached or exceeded" in str(exc_info.value)

    # Current open risk exceeding ceiling
    with pytest.raises(PortfolioRiskLimitExceededError):
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
            capital_config=capital_config,
            current_open_risk_dollars=6_500.0,
        )


# ============================================================================
# 5. Fail-Closed Handling & Invalid Inputs
# ============================================================================


def test_insufficient_capital_for_single_share_raises_error(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Fail closed with InsufficientCapitalError if equity cannot support even 1 discrete share."""
    # Equity = $100 -> Risk budget = $1.00. Effective risk per share = $5.50 -> 0 shares
    with pytest.raises(InsufficientCapitalError) as exc_info:
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=100.0,
            risk_config=risk_config,
            capital_config=capital_config,
        )
    assert "less than 1 minimum executable share" in str(exc_info.value)


@pytest.mark.parametrize("bad_equity", [0.0, -100.0, -1.0])
def test_invalid_equity_raises_invalid_sizing_input_error(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    bad_equity: float,
) -> None:
    """Non-positive equity raises InvalidSizingInputError."""
    with pytest.raises(InvalidSizingInputError):
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=bad_equity,
            risk_config=risk_config,
            capital_config=capital_config,
        )


def test_negative_auxiliary_inputs_raise_invalid_sizing_input_error(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Negative open risk or deployed capital raises InvalidSizingInputError."""
    with pytest.raises(InvalidSizingInputError):
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=100_000.0,
            risk_config=risk_config,
            capital_config=capital_config,
            current_open_risk_dollars=-10.0,
        )

    with pytest.raises(InvalidSizingInputError):
        calculate_position_size(
            entry_order=sample_entry_order,
            portfolio_equity=100_000.0,
            risk_config=risk_config,
            capital_config=capital_config,
            current_deployed_core_dollars=-50.0,
        )

    with pytest.raises(InvalidSizingInputError):
        verify_concentration_limit(
            market_value=-100.0,
            portfolio_equity=100_000.0,
            risk_config=risk_config,
        )

    with pytest.raises(InvalidSizingInputError):
        verify_concentration_limit(
            market_value=10_000.0,
            portfolio_equity=0.0,
            risk_config=risk_config,
        )


# ============================================================================
# 6. Adverse Gap Stress Scenarios (FR-003)
# ============================================================================


def test_adverse_gap_stress_scenarios_calculated(
    sample_entry_order: CoreEntryOrder,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Adverse gap stress scenarios (5%, 10%, 20%) are computed and attached to decision."""
    portfolio_equity = 100_000.0
    decision = calculate_position_size(
        entry_order=sample_entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
        gap_scenarios=(0.05, 0.10, 0.20),
    )

    assert len(decision.gap_scenarios) == 3
    shares = decision.allocated_shares  # 181

    # 5% gap down: entry $100 * 0.05 = $5.00 loss (equals nominal stop of $5.00)
    gap_5 = decision.gap_scenarios[0]
    assert gap_5.gap_down_pct == 0.05
    assert gap_5.loss_per_share == 5.0
    assert gap_5.total_scenario_loss_dollars == round(shares * 5.0, 4)
    assert gap_5.scenario_loss_pct_equity == round((shares * 5.0) / portfolio_equity, 6)

    # 10% gap down: entry $100 * 0.10 = $10.00 loss (exceeds nominal stop)
    gap_10 = decision.gap_scenarios[1]
    assert gap_10.gap_down_pct == 0.10
    assert gap_10.loss_per_share == 10.0
    assert gap_10.total_scenario_loss_dollars == round(shares * 10.0, 4)
    assert gap_10.scenario_loss_pct_equity == round((shares * 10.0) / portfolio_equity, 6)

    # 20% gap down: entry $100 * 0.20 = $20.00 loss
    gap_20 = decision.gap_scenarios[2]
    assert gap_20.gap_down_pct == 0.20
    assert gap_20.loss_per_share == 20.0
    assert gap_20.total_scenario_loss_dollars == round(shares * 20.0, 4)
    assert gap_20.scenario_loss_pct_equity == round((shares * 20.0) / portfolio_equity, 6)


# ============================================================================
# 7. End-to-End Pipeline Integration
# ============================================================================


def test_end_to_end_signal_entry_sizing_position_pipeline(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
) -> None:
    """Full lifecycle:
    BarSeries -> Signal -> CoreEntryOrder -> PositionSizingDecision -> CorePosition.
    """
    generator = DeterministicSignalGenerator(strategy_config)

    # Build breakout series
    bars = [
        Bar(
            timestamp=datetime(2026, 9, 1, 10, i, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=98.0,
            close=100.0,
            volume=10_000.0,
        )
        for i in range(10)
    ]
    # Breakout bar
    breakout_bar = Bar(
        timestamp=datetime(2026, 9, 1, 10, 11, tzinfo=UTC),
        open=102.0,
        high=115.0,
        low=101.0,
        close=112.0,
        volume=50_000.0,
    )
    bars.append(breakout_bar)
    series = BarSeries(symbol="NVDA", timeframe="1d", bars=tuple(bars))

    signal = generator.generate(series)
    assert signal is not None

    entry_order = create_entry_from_signal(
        signal=signal,
        strategy_config=strategy_config,
        risk_config=risk_config,
    )
    assert entry_order.symbol == "NVDA"
    assert entry_order.entry_price == 112.0
    assert entry_order.invalidation.price < 112.0

    portfolio_equity = 250_000.0
    decision = calculate_position_size(
        entry_order=entry_order,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
        capital_config=capital_config,
    )

    assert decision.allocated_shares >= 1
    assert decision.market_value <= portfolio_equity * risk_config.max_position_concentration_pct
    assert decision.effective_risk_dollars <= portfolio_equity * risk_config.max_risk_per_trade_pct

    # Instantiate CorePosition with sized quantity
    position = CorePosition(
        position_id="POS-NVDA-001",
        symbol=decision.symbol,
        entry_order=entry_order,
        entry_price=decision.entry_price,
        quantity=decision.allocated_shares,
        invalidation=entry_order.invalidation,
        status=PositionStatus.OPEN,
        opened_at=decision.timestamp,
    )

    assert position.status == PositionStatus.OPEN
    assert position.quantity == decision.allocated_shares
    assert position.entry_price == decision.entry_price
