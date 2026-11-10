"""Tests for Story TRADER-006: Winner-Only Pyramiding & Aggregate Risk.

Verifies:
- FR-005 & FR-006: Winner-only pyramiding; strict anti-averaging down.
- AC-001: Losing or declining positions strictly rejected from pyramiding.
- AC-002: Qualifying winners (unrealized profit >= 1.0R) can receive additions;
  1R risk anchor remains invariant across stop ratchets.
- AC-003: Concentration limits block additional exposure without forcing liquidation.
- AC-004: Pure mathematical determinism without LLM interpretation (INV-006).
- AC-005: Proposed additions evaluated against aggregate position and portfolio risk ceilings.
- INV-005: Stop ratchets on pyramid additions must be tighten-only.
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import RiskConfig, StrategyConfig
from toso.entry.errors import InvalidationLooseningError
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.exit.evaluator import evaluate_position_exit, execute_invalidation_exit
from toso.exit.models import CorePosition, PositionStatus
from toso.pyramiding import (
    AggregateRiskLimitExceededError,
    MaxPyramidAddsExceededError,
    PortfolioRiskLimitExceededError,
    PyramidConcentrationExceededError,
    PyramidNotEligibleError,
    PyramidOrder,
    apply_pyramid_to_position,
    calculate_aggregate_risk,
    create_pyramid_order,
    evaluate_pyramid_eligibility,
)
from toso.signals.models import Bar


@pytest.fixture
def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="CORE-TREND-PYRAMID-TEST",
        timeframe="1d",
        fast_ma_period=5,
        slow_ma_period=10,
        atr_period=5,
        breakout_lookback_bars=5,
        invalidation_atr_multiple=2.0,
        pyramid_min_profit_r=1.0,  # Must have >= 1.0R profit
        max_pyramid_adds=3,  # Max 3 additions
    )


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,  # 1% per add
        max_aggregate_position_risk_pct=0.02,  # 2% aggregate position risk
        max_portfolio_risk_pct=0.06,  # 6% portfolio risk
        max_position_concentration_pct=0.20,  # 20% max concentration
        slippage_buffer_pct=0.005,  # 0.5% slippage buffer
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def base_position() -> CorePosition:
    # Initial: 100 shares @ $100, stop @ $95
    # 1R = 100 * ($100 - $95) = $500.00
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    invalidation = InvalidationDefinition(
        price=95.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=False,
        reason="Initial entry stop at 95.00",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0,
        invalidation_price=95.0,
        slippage_buffer_pct=0.005,
    )
    entry_order = CoreEntryOrder(
        order_id="ORD-BASE-001",
        symbol="NVDA",
        timestamp=now,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_accounting,
        reason="Initial breakout entry",
    )
    return CorePosition(
        position_id="POS-NVDA-001",
        symbol="NVDA",
        entry_order=entry_order,
        entry_price=100.0,
        quantity=100,
        invalidation=invalidation,
        status=PositionStatus.OPEN,
        opened_at=now,
    )


# ============================================================================
# 1. AC-001: Anti-Averaging Down & Losing/Declining Positions
# ============================================================================


def test_ac001_declining_position_rejected(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-001: A position trading below initial entry is strictly ineligible to pyramid."""
    eligibility = evaluate_pyramid_eligibility(
        position=base_position,
        current_price=98.0,  # Below $100
        strategy_config=strategy_config,
    )
    assert not eligibility.is_eligible
    assert "Never Average Down" in eligibility.rejection_reason

    # Also verify create_pyramid_order raises PyramidNotEligibleError fail-closed
    with pytest.raises(PyramidNotEligibleError) as exc_info:
        create_pyramid_order(
            position=base_position,
            add_price=98.0,
            new_invalidation=base_position.invalidation,
            portfolio_equity=100_000.0,
            risk_config=risk_config,
            strategy_config=strategy_config,
        )
    assert "Never Average Down" in str(exc_info.value)


def test_ac001_breakeven_position_rejected(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-001: A position trading at entry price (0R profit) cannot receive additions."""
    eligibility = evaluate_pyramid_eligibility(
        position=base_position,
        current_price=100.0,  # Exactly at entry
        strategy_config=strategy_config,
    )
    assert not eligibility.is_eligible
    assert "Never Average Down" in eligibility.rejection_reason


def test_ac001_price_below_blended_cost_basis_rejected(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-001: If position already has a pyramid add, price below blended average is rejected."""
    # Add 100 shares @ $120 -> total 200 shares, average entry = $110
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet = InvalidationDefinition(
        price=105.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Pyramid stop ratchet",
    )
    pyr_order = PyramidOrder(
        pyramid_order_id="PYR-001",
        position_id=base_position.position_id,
        symbol="NVDA",
        timestamp=now,
        side="BUY",
        add_index=1,
        price=120.0,
        quantity=100,
        new_invalidation=stop_ratchet,
        explanation="First pyramid",
    )
    pyramided_pos = apply_pyramid_to_position(base_position, pyr_order)
    assert pyramided_pos.average_entry_price == 110.0

    # Test price = $105 (above initial $100, but below blended average $110)
    eligibility = evaluate_pyramid_eligibility(
        position=pyramided_pos,
        current_price=105.0,
        strategy_config=strategy_config,
    )
    assert not eligibility.is_eligible
    assert "at or below cost basis" in eligibility.rejection_reason


def test_ac001_price_below_last_add_price_rejected(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-001: Pyramiding requires strictly upward additions (above previous add price)."""
    # Position avg entry = $110, last add = $120
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet = InvalidationDefinition(
        price=105.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Pyramid stop ratchet",
    )
    pyr_order = PyramidOrder(
        pyramid_order_id="PYR-001",
        position_id=base_position.position_id,
        symbol="NVDA",
        timestamp=now,
        side="BUY",
        add_index=1,
        price=120.0,
        quantity=100,
        new_invalidation=stop_ratchet,
        explanation="First pyramid",
    )
    pyramided_pos = apply_pyramid_to_position(base_position, pyr_order)

    # Test price = $115 (above average entry $110, but below last add $120)
    eligibility = evaluate_pyramid_eligibility(
        position=pyramided_pos,
        current_price=115.0,
        strategy_config=strategy_config,
    )
    assert not eligibility.is_eligible
    assert "strictly upward additions" in eligibility.rejection_reason


def test_ac001_invalidated_or_closed_position_cannot_pyramid(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-001: Inactive positions cannot receive pyramid additions."""
    invalidated_pos = CorePosition(
        position_id=base_position.position_id,
        symbol=base_position.symbol,
        entry_order=base_position.entry_order,
        entry_price=base_position.entry_price,
        quantity=base_position.quantity,
        invalidation=base_position.invalidation,
        status=PositionStatus.INVALIDATED,
        opened_at=base_position.opened_at,
    )
    eligibility = evaluate_pyramid_eligibility(
        position=invalidated_pos,
        current_price=115.0,
        strategy_config=strategy_config,
    )
    assert not eligibility.is_eligible
    assert "not OPEN" in eligibility.rejection_reason


# ============================================================================
# 2. AC-002: Winner Qualification & 1R Anchor Invariance
# ============================================================================


def test_ac002_winner_qualification_threshold(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-002: Position qualifies when unrealized profit >= 1.0R ($500)."""
    # 1R = 100 * ($100 - $95) = $500.
    # At $104.00, profit = $400 (0.80R) -> Ineligible
    el_sub_1r = evaluate_pyramid_eligibility(
        position=base_position,
        current_price=104.0,
        strategy_config=strategy_config,
    )
    assert not el_sub_1r.is_eligible
    assert el_sub_1r.r_multiple == 0.80
    assert "below required threshold" in el_sub_1r.rejection_reason

    # At $105.00, profit = $500 (1.00R) -> Eligible
    el_1r = evaluate_pyramid_eligibility(
        position=base_position,
        current_price=105.0,
        strategy_config=strategy_config,
    )
    assert el_1r.is_eligible
    assert el_1r.r_multiple == 1.00
    assert el_1r.rejection_reason is None


def test_ac002_1r_anchor_invariant_across_ratchets(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-002 & FINDING-002: 1R anchor remains strictly invariant across multiple stop ratchets."""
    # Initial 1R = 100 * ($100 - $95) = $500.00
    assert base_position.entry_order.invalidation.price == 95.0

    # Advance price to $110 (profit = $1,000 = 2.0R)
    # Add #1 with stop ratcheted to $104 (tighter than initial $95 and above entry $100)
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet_1 = InvalidationDefinition(
        price=104.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Ratchet 1",
    )
    pyr_1 = create_pyramid_order(
        position=base_position,
        add_price=110.0,
        new_invalidation=stop_ratchet_1,
        portfolio_equity=100_000.0,
        risk_config=risk_config,
        strategy_config=strategy_config,
        target_add_quantity=50,
    )
    pos_after_pyr1 = apply_pyramid_to_position(base_position, pyr_1)

    # Active stop is now $104, total shares = 150, avg entry = (10000 + 5500)/150 = 103.3333
    assert pos_after_pyr1.invalidation.price == 104.0

    # Advance price to $120. Evaluate eligibility for Add #2.
    # CRITICAL: 1R must STILL be exactly $500.00, NOT computed from the ratcheted stop ($104)!
    el_check_2 = evaluate_pyramid_eligibility(
        position=pos_after_pyr1,
        current_price=120.0,
        strategy_config=strategy_config,
    )
    assert el_check_2.initial_risk_dollars == 500.00
    assert el_check_2.is_eligible


# ============================================================================
# 3. AC-003: Concentration Gating Without Forcing Liquidation
# ============================================================================


def test_ac003_concentration_blocks_add_without_liquidating_winner(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-003: Concentration breach blocks the addition; existing winner remains untouched."""
    portfolio_equity = 50_000.0
    # Max concentration = 20% = $10,000.
    # Base position: 100 shares @ $100 = $10,000 market value (already at 20% cap).
    # Any addition will breach $10,000 market value cap.
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet = InvalidationDefinition(
        price=105.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Pyramid stop ratchet",
    )

    with pytest.raises(PyramidConcentrationExceededError) as exc_info:
        create_pyramid_order(
            position=base_position,
            add_price=110.0,
            new_invalidation=stop_ratchet,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
            strategy_config=strategy_config,
            target_add_quantity=10,
        )

    assert "exceeding maximum concentration cap" in str(exc_info.value)
    assert "existing winning position remains open without liquidation" in str(exc_info.value)

    # Verify existing position remains completely OPEN and unmodified
    assert base_position.status == PositionStatus.OPEN
    assert base_position.quantity == 100
    assert len(base_position.pyramids) == 0


# ============================================================================
# 4. AC-004 & Determinism
# ============================================================================


def test_ac004_pure_mathematical_determinism(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-004: Identical inputs produce identical risk assessments and orders (INV-006)."""
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet = InvalidationDefinition(
        price=102.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Deterministic ratchet",
    )

    res1 = calculate_aggregate_risk(
        position=base_position,
        add_quantity=50,
        add_price=110.0,
        new_stop_price=102.0,
        portfolio_equity=100_000.0,
        risk_config=risk_config,
    )
    res2 = calculate_aggregate_risk(
        position=base_position,
        add_quantity=50,
        add_price=110.0,
        new_stop_price=102.0,
        portfolio_equity=100_000.0,
        risk_config=risk_config,
    )

    assert res1.weighted_average_entry == res2.weighted_average_entry
    assert res1.effective_aggregate_risk_dollars == res2.effective_aggregate_risk_dollars
    assert res1.resulting_concentration_pct == res2.resulting_concentration_pct

    # Also verify deterministic order generation
    pyr1 = create_pyramid_order(
        position=base_position,
        add_price=110.0,
        new_invalidation=stop_ratchet,
        portfolio_equity=100_000.0,
        risk_config=risk_config,
        strategy_config=strategy_config,
        target_add_quantity=50,
    )
    assert pyr1.quantity == 50
    assert pyr1.price == 110.0


# ============================================================================
# 5. AC-005: Aggregate Position & Portfolio Risk Ceilings
# ============================================================================


def test_ac005_aggregate_position_risk_limit_exceeded(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
) -> None:
    """AC-005: Rejection when proposed addition causes combined position risk to exceed cap."""
    portfolio_equity = 100_000.0
    # Custom config: max_aggregate_position_risk_pct = 0.01 (1%), concentration cap = 25%
    cfg_tight_agg = RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.01,  # $1,000 cap
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.25,  # $25,000 cap
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    stop_ratchet = InvalidationDefinition(
        price=95.0,  # Keeping initial stop at $95
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=False,
        reason="Wide stop",
    )

    # Adding 50 shares @ $110 with stop at $95:
    # Combined shares = 150. Market value = $16,500 <= $25,000 (concentration passes).
    # Weighted avg entry = $103.3333. Effective aggregate risk = $1,332.50 > $1,000 cap!
    with pytest.raises(AggregateRiskLimitExceededError) as exc_info:
        create_pyramid_order(
            position=base_position,
            add_price=110.0,
            new_invalidation=stop_ratchet,
            portfolio_equity=portfolio_equity,
            risk_config=cfg_tight_agg,
            strategy_config=strategy_config,
            target_add_quantity=50,
        )

    assert "exceeds maximum aggregate position risk limit" in str(exc_info.value)


def test_ac005_portfolio_risk_limit_exceeded(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-005: Rejection when proposed addition causes portfolio open risk to exceed 6% cap."""
    portfolio_equity = 100_000.0
    # Max portfolio risk = 6% = $6,000.
    # Current portfolio open risk is already $5,900.
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    # Stop tightened to $96.00 (from $95.00)
    stop_ratchet = InvalidationDefinition(
        price=96.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Ratchet stop to 96",
    )

    with pytest.raises(PortfolioRiskLimitExceededError) as exc_info:
        create_pyramid_order(
            position=base_position,
            add_price=110.0,
            new_invalidation=stop_ratchet,
            portfolio_equity=portfolio_equity,
            risk_config=risk_config,
            strategy_config=strategy_config,
            current_open_risk_dollars=5_900.0,
            target_add_quantity=50,
        )

    assert "exceeds maximum portfolio risk ceiling" in str(exc_info.value)


# ============================================================================
# 6. Invariants & Caps: Anti-Loosening & Max Adds
# ============================================================================


def test_inv005_loosening_stop_on_pyramid_rejected(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """INV-005: Lowering the stop during pyramiding is strictly prohibited (tighten-only)."""
    # Base position active stop is $95.00
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    loosened_stop = InvalidationDefinition(
        price=94.00,  # Lower than $95.00!
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=False,
        reason="Illegal loosening",
    )

    with pytest.raises(InvalidationLooseningError) as exc_info:
        create_pyramid_order(
            position=base_position,
            add_price=110.0,
            new_invalidation=loosened_stop,
            portfolio_equity=100_000.0,
            risk_config=risk_config,
            strategy_config=strategy_config,
            target_add_quantity=10,
        )
    assert "cannot loosen current position stop" in str(exc_info.value)


def test_max_pyramid_adds_cap_enforced(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """StrategyConfig.max_pyramid_adds (3) strictly blocks a 4th addition."""
    pos = base_position
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)

    # Apply 3 sequential additions
    for i in range(1, 4):
        add_price = 100.0 + (i * 10.0)  # 110, 120, 130
        stop_price = 95.0 + (i * 8.0)  # 103, 111, 119
        stop_def = InvalidationDefinition(
            price=stop_price,
            invalidation_type="ATR_STOP",
            atr_multiple=2.0,
            reference_atr=2.5,
            established_at=now,
            is_ratcheted=True,
            reason=f"Ratchet {i}",
        )
        pyr = create_pyramid_order(
            position=pos,
            add_price=add_price,
            new_invalidation=stop_def,
            portfolio_equity=200_000.0,
            risk_config=risk_config,
            strategy_config=strategy_config,
            target_add_quantity=10,
        )
        pos = apply_pyramid_to_position(pos, pyr)

    assert len(pos.pyramids) == 3

    # Attempt 4th addition at $140
    fourth_stop = InvalidationDefinition(
        price=125.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Ratchet 4",
    )
    with pytest.raises(MaxPyramidAddsExceededError) as exc_info:
        create_pyramid_order(
            position=pos,
            add_price=140.0,
            new_invalidation=fourth_stop,
            portfolio_equity=200_000.0,
            risk_config=risk_config,
            strategy_config=strategy_config,
            target_add_quantity=10,
        )
    assert "Maximum pyramid additions (3) already reached" in str(exc_info.value)


# ============================================================================
# 7. End-to-End Multi-Leg Position and Exit Lifecycle
# ============================================================================


def test_end_to_end_pyramided_position_and_exit_realization(
    base_position: CorePosition,
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """Full lifecycle: Entry -> Pyramid Add -> Stop Breach -> Realizes combined PnL."""
    now = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)

    # Initial: 100 shares @ $100
    # Add: 50 shares @ $110 with stop ratcheted to $104
    stop_def = InvalidationDefinition(
        price=104.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=now,
        is_ratcheted=True,
        reason="Pyramid stop ratchet",
    )
    pyr_order = create_pyramid_order(
        position=base_position,
        add_price=110.0,
        new_invalidation=stop_def,
        portfolio_equity=100_000.0,
        risk_config=risk_config,
        strategy_config=strategy_config,
        target_add_quantity=50,
    )
    combined_pos = apply_pyramid_to_position(base_position, pyr_order)

    # Combined metrics: 150 shares, average entry = (100*100 + 50*110)/150 = 15500/150 = $103.3333
    assert combined_pos.quantity == 150
    assert combined_pos.average_entry_price == 103.3333
    assert combined_pos.invalidation.price == 104.0

    # Market drops: Bar low breaches stop ($104.00) with low=$103.00
    exit_bar = Bar(
        timestamp=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        open=106.0,
        high=107.0,
        low=103.0,
        close=103.5,
        volume=25_000.0,
    )

    invalidated_pos, exit_order = evaluate_position_exit(combined_pos, exit_bar)
    assert exit_order is not None
    assert exit_order.quantity == 150
    assert exit_order.trigger_price == 104.0
    assert invalidated_pos.status == PositionStatus.INVALIDATED

    # Execute exit fill at $104.00
    closed_pos = execute_invalidation_exit(
        position=invalidated_pos,
        exit_order=exit_order,
        fill_price=104.0,
        fill_timestamp=datetime(2026, 9, 15, 10, 5, tzinfo=UTC),
    )

    assert closed_pos.status == PositionStatus.CLOSED
    # Realized PnL = (104.0 - 103.3333) * 150 = 0.6667 * 150 = $100.005 -> rounded to $100.005
    expected_pnl = round((104.0 - 103.3333) * 150, 4)
    assert closed_pos.realized_pnl == expected_pnl
    assert closed_pos.realized_pnl > 0  # Protected profit on winner campaign!
