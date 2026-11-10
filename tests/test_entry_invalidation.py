"""Tests for Story TRADER-003: Entry & Invalidation Definition.

Verifies:
- AC-001: Every Core position requires deterministic invalidation before execution.
- AC-002: Invalidation calculation is deterministic and cross-checked against config (INV-006).
- AC-003: Anti-loosening rule (INV-005) enforced (tighten-only) and agent overrides rejected.
- AC-004: Explicit adverse gap and execution slippage accounting in entry risk model.
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import RiskConfig, StrategyConfig
from toso.entry.errors import (
    AgentOverrideRejectedError,
    InvalidationLooseningError,
    InvalidEntryOrderError,
    InvalidInvalidationError,
    MissingInvalidationError,
)
from toso.entry.evaluator import (
    calculate_deterministic_invalidation,
    create_entry_from_signal,
    ratchet_invalidation,
    reject_agent_invalidation_override,
)
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.models import (
    Bar,
    BarSeries,
    SignalIndicatorSnapshot,
    SignalType,
    TradeSignal,
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
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.005,  # 0.5% slippage buffer
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def sample_timestamp() -> datetime:
    return datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


# ============================================================================
# AC-001: Every Core Position Requires a Deterministic Invalidation
# ============================================================================


def test_ac001_valid_entry_order_created_with_recorded_invalidation(
    sample_timestamp: datetime,
) -> None:
    """AC-001: Entry order is successfully created with recorded deterministic invalidation."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Deterministic 2.0x ATR stop",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0, invalidation_price=90.0, slippage_buffer_pct=0.005
    )

    order = CoreEntryOrder(
        order_id="ENTRY-AAPL-001",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_accounting,
        reason="Donchian breakout",
    )

    assert order.order_id == "ENTRY-AAPL-001"
    assert order.invalidation.price == 90.0
    assert order.entry_price == 100.0
    assert order.risk_accounting.nominal_risk_per_share == 10.0
    assert order.risk_accounting.slippage_allowance_per_share == 0.50
    assert order.risk_accounting.effective_risk_per_share == 10.50


def test_ac001_direct_construction_missing_invalidation_fails_closed(
    sample_timestamp: datetime,
) -> None:
    """AC-001: Missing invalidation on direct construction raises MissingInvalidationError."""
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0, invalidation_price=90.0, slippage_buffer_pct=0.005
    )

    with pytest.raises(
        MissingInvalidationError, match="CoreEntryOrder requires a recorded InvalidationDefinition"
    ):
        CoreEntryOrder.model_validate(
            {
                "order_id": "ENTRY-AAPL-002",
                "symbol": "AAPL",
                "timestamp": sample_timestamp,
                "side": "BUY",
                "entry_price": 100.0,
                "invalidation": None,
                "risk_accounting": risk_accounting,
                "reason": "Test missing stop",
            }
        )


def test_ac001_invalidation_greater_or_equal_to_entry_price_fails_closed(
    sample_timestamp: datetime,
) -> None:
    """AC-001: Initial invalidation price >= entry price fails closed."""
    # Stop equal to entry
    invalidation_equal = InvalidationDefinition(
        price=100.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Invalid stop equal to entry",
    )
    with pytest.raises(InvalidInvalidationError, match="cannot be greater than or equal to entry"):
        CoreEntryOrder(
            order_id="ENTRY-AAPL-003",
            symbol="AAPL",
            timestamp=sample_timestamp,
            side="BUY",
            entry_price=100.0,
            invalidation=invalidation_equal,
            risk_accounting=GapSlippageRiskModel(
                nominal_risk_per_share=1.0,
                slippage_buffer_pct=0.005,
                slippage_allowance_per_share=0.5,
                effective_risk_per_share=1.5,
            ),
            reason="Equal stop test",
        )

    # Stop above entry
    invalidation_above = InvalidationDefinition(
        price=105.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Invalid stop above entry",
    )
    with pytest.raises(InvalidInvalidationError, match="cannot be greater than or equal to entry"):
        CoreEntryOrder(
            order_id="ENTRY-AAPL-004",
            symbol="AAPL",
            timestamp=sample_timestamp,
            side="BUY",
            entry_price=100.0,
            invalidation=invalidation_above,
            risk_accounting=GapSlippageRiskModel(
                nominal_risk_per_share=1.0,
                slippage_buffer_pct=0.005,
                slippage_allowance_per_share=0.5,
                effective_risk_per_share=1.5,
            ),
            reason="Above stop test",
        )


def test_ac001_non_positive_invalidation_fails_closed(
    sample_timestamp: datetime,
) -> None:
    """AC-001: Non-positive invalidation price raises InvalidInvalidationError."""
    with pytest.raises(InvalidInvalidationError, match="Invalidation price must be positive"):
        InvalidationDefinition(
            price=-5.0,
            invalidation_type="ATR_STOP",
            atr_multiple=2.0,
            reference_atr=5.0,
            established_at=sample_timestamp,
            is_ratcheted=False,
            reason="Negative stop",
        )

    with pytest.raises(InvalidInvalidationError, match="Invalidation price must be positive"):
        InvalidationDefinition(
            price=0.0,
            invalidation_type="ATR_STOP",
            atr_multiple=2.0,
            reference_atr=5.0,
            established_at=sample_timestamp,
            is_ratcheted=False,
            reason="Zero stop",
        )


def test_ac001_non_positive_entry_price_fails_closed(
    sample_timestamp: datetime,
) -> None:
    """AC-001: Non-positive entry price raises InvalidEntryOrderError."""
    invalidation = InvalidationDefinition(
        price=10.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Valid stop",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=50.0, invalidation_price=10.0, slippage_buffer_pct=0.005
    )

    with pytest.raises(InvalidEntryOrderError, match="Entry price must be positive"):
        CoreEntryOrder.model_validate(
            {
                "order_id": "ENTRY-005",
                "symbol": "AAPL",
                "timestamp": sample_timestamp,
                "side": "BUY",
                "entry_price": -10.0,
                "invalidation": invalidation,
                "risk_accounting": risk_accounting,
                "reason": "Negative entry test",
            }
        )


# ============================================================================
# AC-002: Deterministic Calculation & Verification (INV-006)
# ============================================================================


def test_ac002_deterministic_invalidation_calculation_reproducible(
    strategy_config: StrategyConfig,
    sample_timestamp: datetime,
) -> None:
    """AC-002: Invalidation calculation produces identical values across repeated runs."""
    entry_price = 150.0
    atr = 3.25
    expected_stop = round(entry_price - (strategy_config.invalidation_atr_multiple * atr), 4)

    res1 = calculate_deterministic_invalidation(entry_price, atr, strategy_config, sample_timestamp)
    res2 = calculate_deterministic_invalidation(entry_price, atr, strategy_config, sample_timestamp)

    assert res1.price == expected_stop
    assert res1.price == res2.price
    assert res1 == res2
    assert res1.price == 143.50


def test_ac002_create_entry_from_signal_cross_checks_invalidation(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
    sample_timestamp: datetime,
) -> None:
    """AC-002: create_entry_from_signal verifies signal invalidation matches strategy formula."""
    indicators = SignalIndicatorSnapshot(
        fast_ma=140.0,
        slow_ma=130.0,
        atr=4.0,
        donchian_high=145.0,
        donchian_low=120.0,
        current_close=150.0,
    )
    # Expected stop: 150.0 - (2.0 * 4.0) = 142.0
    valid_signal = TradeSignal(
        symbol="MSFT",
        timestamp=sample_timestamp,
        signal_type=SignalType.BULLISH_ENTRY,
        price=150.0,
        invalidation_price=142.0,
        indicators=indicators,
        reason="Breakout confirmed",
    )

    order = create_entry_from_signal(valid_signal, strategy_config, risk_config)

    assert order.symbol == "MSFT"
    assert order.entry_price == 150.0
    assert order.invalidation.price == 142.0
    assert order.risk_accounting.nominal_risk_per_share == 8.0
    assert order.risk_accounting.slippage_allowance_per_share == 0.75  # 150 * 0.005
    assert order.risk_accounting.effective_risk_per_share == 8.75


def test_ac002_create_entry_from_signal_rejects_tampered_invalidation(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
    sample_timestamp: datetime,
) -> None:
    """AC-002 / INV-006: Rejects signal if invalidation diverges from deterministic formula."""
    indicators = SignalIndicatorSnapshot(
        fast_ma=140.0,
        slow_ma=130.0,
        atr=4.0,
        donchian_high=145.0,
        donchian_low=120.0,
        current_close=150.0,
    )
    # Formula demands 142.0, but signal has manipulated 148.0 (too tight/loose)
    tampered_signal = TradeSignal(
        symbol="MSFT",
        timestamp=sample_timestamp,
        signal_type=SignalType.BULLISH_ENTRY,
        price=150.0,
        invalidation_price=148.0,
        indicators=indicators,
        reason="Tampered stop",
    )

    with pytest.raises(InvalidInvalidationError, match="does not match deterministic calculation"):
        create_entry_from_signal(tampered_signal, strategy_config, risk_config)


def test_ac002_create_entry_from_non_entry_signal_fails_closed(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
    sample_timestamp: datetime,
) -> None:
    """AC-002: Signals that are HOLD or BEARISH_EXIT cannot create a CoreEntryOrder."""
    indicators = SignalIndicatorSnapshot(
        fast_ma=140.0,
        slow_ma=130.0,
        atr=4.0,
        donchian_high=145.0,
        donchian_low=120.0,
        current_close=150.0,
    )
    hold_signal = TradeSignal(
        symbol="MSFT",
        timestamp=sample_timestamp,
        signal_type=SignalType.HOLD,
        price=150.0,
        invalidation_price=142.0,
        indicators=indicators,
        reason="Holding",
    )

    with pytest.raises(InvalidEntryOrderError, match="Only BULLISH_ENTRY signals are eligible"):
        create_entry_from_signal(hold_signal, strategy_config, risk_config)


def test_ac002_end_to_end_integration_with_signal_generator(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """AC-002: End-to-end: BarSeries -> SignalGenerator -> create_entry_from_signal."""
    # Build 15 chronological bars with upward breakout
    bars = []
    base_ts = 1700000000
    for i in range(15):
        price = 100.0 + (i * 2.0)
        bars.append(
            Bar(
                timestamp=datetime.fromtimestamp(base_ts + (i * 86400), tz=UTC),
                open=price - 0.5,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=10000.0,
            )
        )

    # Make the last bar decisively break out above the 5-bar Donchian channel
    prev_high = max(b.high for b in bars[-6:-1])
    breakout_close = prev_high + 5.0
    bars[-1] = Bar(
        timestamp=bars[-1].timestamp,
        open=breakout_close - 1.0,
        high=breakout_close + 2.0,
        low=breakout_close - 2.0,
        close=breakout_close,
        volume=20000.0,
    )

    series = BarSeries(symbol="NVDA", bars=tuple(bars))
    generator = DeterministicSignalGenerator(strategy_config)
    signal = generator.generate(series)

    assert signal.signal_type == SignalType.BULLISH_ENTRY
    assert signal.invalidation_price is not None

    entry_order = create_entry_from_signal(signal, strategy_config, risk_config)

    assert entry_order.symbol == "NVDA"
    assert entry_order.entry_price == series.latest_bar.close
    assert entry_order.invalidation.price == signal.invalidation_price
    assert entry_order.risk_accounting.effective_risk_per_share > 0.0


# ============================================================================
# AC-003: Anti-Loosening Invariant (INV-005) & Agent Boundary
# ============================================================================


def test_ac003_anti_loosening_rejects_lowering_stop(
    sample_timestamp: datetime,
    risk_config: RiskConfig,
) -> None:
    """AC-003 / INV-005: Lowering invalidation stop fails closed with InvalidationLooseningError."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Initial stop",
    )
    order = CoreEntryOrder(
        order_id="ENTRY-AAPL-006",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=GapSlippageRiskModel.compute(100.0, 90.0, 0.005),
        reason="Entry",
    )

    # Attempt to loosen stop from 90.0 down to 85.0
    with pytest.raises(
        InvalidationLooseningError, match="Anti-loosening rule .* strictly prohibits widening risk"
    ):
        ratchet_invalidation(
            current_order=order,
            new_stop_price=85.0,
            timestamp=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
            reason="Discretionary room to breathe",
            risk_config=risk_config,
        )


def test_ac003_ratchet_tighter_produces_new_immutable_order(
    sample_timestamp: datetime,
    risk_config: RiskConfig,
) -> None:
    """AC-003 / INV-005: Tightening stop succeeds, returning order with is_ratcheted=True."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Initial stop",
    )
    initial_order = CoreEntryOrder(
        order_id="ENTRY-AAPL-007",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=GapSlippageRiskModel.compute(100.0, 90.0, 0.005),
        reason="Entry",
    )

    new_timestamp = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    ratcheted_order = ratchet_invalidation(
        current_order=initial_order,
        new_stop_price=95.0,
        timestamp=new_timestamp,
        reason="Trailing stop upward on bar confirmation",
        risk_config=risk_config,
    )

    # Verify original order unchanged (pure immutability)
    assert initial_order.invalidation.price == 90.0
    assert initial_order.invalidation.is_ratcheted is False
    assert initial_order.risk_accounting.nominal_risk_per_share == 10.0

    # Verify new ratcheted order has tighter stop and reduced risk
    assert ratcheted_order.invalidation.price == 95.0
    assert ratcheted_order.invalidation.is_ratcheted is True
    assert ratcheted_order.risk_accounting.nominal_risk_per_share == 5.0
    assert ratcheted_order.risk_accounting.effective_risk_per_share == 5.50
    assert "Trailing stop upward" in ratcheted_order.reason


def test_ac003_ratchet_can_lock_in_profit_above_entry(
    sample_timestamp: datetime,
    risk_config: RiskConfig,
) -> None:
    """AC-003: Ratcheting stop above entry price locks in gains with 0 nominal capital risk."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Initial stop",
    )
    order = CoreEntryOrder(
        order_id="ENTRY-AAPL-008",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=GapSlippageRiskModel.compute(100.0, 90.0, 0.005),
        reason="Entry",
    )

    # Ratchet stop above entry to 105.0 as price rallied
    ratcheted = ratchet_invalidation(
        current_order=order,
        new_stop_price=105.0,
        timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        reason="Trailing above breakeven to lock in profit",
        risk_config=risk_config,
    )

    assert ratcheted.invalidation.price == 105.0
    assert ratcheted.invalidation.is_ratcheted is True
    assert ratcheted.risk_accounting.nominal_risk_per_share == 0.0  # Zero capital at risk


def test_ac003_agent_override_strictly_rejected(
    sample_timestamp: datetime,
) -> None:
    """AC-003: Any discretionary or Agent attempt to override invalidation fails closed."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Initial stop",
    )
    order = CoreEntryOrder(
        order_id="ENTRY-AAPL-009",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=GapSlippageRiskModel.compute(100.0, 90.0, 0.005),
        reason="Entry",
    )

    with pytest.raises(
        AgentOverrideRejectedError,
        match="Discretionary and LLM agent overrides .* strictly prohibited",
    ):
        reject_agent_invalidation_override(
            current_order=order,
            proposed_price=85.0,
            agent_id="ResearchScientistAgent",
        )


def test_ac003_models_are_strictly_frozen_and_immutable(
    sample_timestamp: datetime,
) -> None:
    """AC-003: Verifies that mutation attempts on models raise ValidationError."""
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=sample_timestamp,
        is_ratcheted=False,
        reason="Initial stop",
    )
    order = CoreEntryOrder(
        order_id="ENTRY-AAPL-010",
        symbol="AAPL",
        timestamp=sample_timestamp,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=GapSlippageRiskModel.compute(100.0, 90.0, 0.005),
        reason="Entry",
    )

    with pytest.raises(Exception):
        order.entry_price = 105.0  # type: ignore

    with pytest.raises(Exception):
        order.invalidation.price = 85.0  # type: ignore


# ============================================================================
# AC-004: Gap & Slippage Risk Accounting
# ============================================================================


def test_ac004_risk_model_incorporates_slippage_buffer() -> None:
    """AC-004: Effective risk per share includes nominal risk and slippage allowance."""
    entry_price = 200.0
    stop_price = 180.0
    buffer_pct = 0.01  # 1% slippage allowance

    risk = GapSlippageRiskModel.compute(
        entry_price=entry_price,
        invalidation_price=stop_price,
        slippage_buffer_pct=buffer_pct,
    )

    assert risk.nominal_risk_per_share == 20.0
    assert risk.slippage_allowance_per_share == 2.0  # 200 * 0.01
    assert risk.effective_risk_per_share == 22.0  # 20 + 2


def test_ac004_adverse_gap_scenario_loss_calculation() -> None:
    """AC-004: Demonstrates adverse overnight gaps beyond stop are explicitly quantified."""
    entry_price = 100.0
    stop_price = 90.0  # 10% nominal stop distance
    risk = GapSlippageRiskModel.compute(
        entry_price=entry_price,
        invalidation_price=stop_price,
        slippage_buffer_pct=0.005,
    )

    # Normal stop exit at or above stop level (e.g. 5% drop) results in nominal risk
    loss_mild = risk.calculate_gap_loss(entry_price, gap_down_pct=0.05)
    assert loss_mild == 10.0  # Protected by stop

    # Catastrophic 25% overnight gap down opens at 75.0 (below stop)
    loss_catastrophic = risk.calculate_gap_loss(entry_price, gap_down_pct=0.25)
    assert loss_catastrophic == 25.0  # Realized loss is full gap distance of $25/share
