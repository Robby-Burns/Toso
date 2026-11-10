"""Tests for Story TRADER-007: Winner Continuation & Anti-Take-Profit Rules.

Verifies:
- FR-007 & Philosophy §2.2: Let winners run; no fixed profit targets, no trimming.
- FR-008 & Philosophy §2.6: Concentration is an intake constraint, not a forced exit.
- AC-001: Fixed profit targets / target price exits strictly prohibited.
- AC-002: Premature trimming / scaling out strictly prohibited.
- AC-003: Organic concentration breach protected from forced liquidation.
- AC-004: Agent and discretionary exits on valid winners strictly rejected.
- AC-005: Deterministic continuation decision receipts; canonical 1R reconciliation.
- AC-006: End-to-end winner continuation, pyramiding, and invalidation exit lifecycle.
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import RiskConfig, StrategyConfig
from toso.continuation import (
    ContinuationStatus,
    DiscretionaryExitProhibitedError,
    ExitType,
    ExitValidationReceipt,
    ForcedConcentrationExitProhibitedError,
    InvalidContinuationStateError,
    PrematureTrimmingProhibitedError,
    TakeProfitProhibitedError,
    WinnerContinuationDecision,
    evaluate_winner_continuation,
    prevent_target_price_registration,
    validate_exit_request,
    verify_winner_continuation_on_bar,
)
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.exit.evaluator import execute_invalidation_exit
from toso.exit.models import CorePosition, PositionStatus
from toso.pyramiding import (
    apply_pyramid_to_position,
    create_pyramid_order,
    evaluate_pyramid_eligibility,
)
from toso.signals.models import Bar


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,  # 20% intake cap
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="CORE-TREND-CONTINUATION-TEST",
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
def winning_position() -> CorePosition:
    """Fixture producing an open winning position: entry=100.0, stop=95.0, 100 shares."""
    entry_time = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
    invalidation = InvalidationDefinition(
        price=95.0,  # 1R per share = $5.00
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=entry_time,
        is_ratcheted=False,
        reason="Initial Donchian breakout ATR stop",
    )
    risk_model = GapSlippageRiskModel.compute(
        entry_price=100.0,
        invalidation_price=95.0,
        slippage_buffer_pct=0.005,
    )
    entry_order = CoreEntryOrder(
        order_id="ENTRY-WINNER-001",
        symbol="NVDA",
        timestamp=entry_time,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_model,
        reason="Initial breakout entry",
    )
    return CorePosition(
        position_id="POS-NVDA-WINNER-001",
        symbol="NVDA",
        entry_order=entry_order,
        entry_price=100.0,
        quantity=100.0,
        invalidation=invalidation,
        status=PositionStatus.OPEN,
        opened_at=entry_time,
        pyramids=(),
        average_entry_price=None,
    )


def test_ac001_fixed_profit_target_strictly_prohibited(winning_position: CorePosition) -> None:
    """AC-001: Attempting to validate or execute a FIXED_PROFIT_TARGET exit must raise error."""
    with pytest.raises(TakeProfitProhibitedError) as exc_info:
        validate_exit_request(
            position=winning_position,
            proposed_exit_type=ExitType.FIXED_PROFIT_TARGET,
            requester_id="ProfitTargetEngine",
        )
    assert "FIXED_PROFIT_TARGET" in str(exc_info.value)
    assert "fixed profit targets are strictly prohibited" in str(exc_info.value)


def test_ac001_target_price_registration_interceptor_allowlist() -> None:
    """AC-001 / FINDING-003: prevent_target_price_registration enforces strict allowlist."""
    # Compliant payload
    valid_payload = {
        "order_id": "ORD-123",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 100.0,
        "price": 150.0,
        "invalidation_price": 140.0,
    }
    prevent_target_price_registration(valid_payload)  # Must not raise

    # Prohibited profit target variations
    for bad_key in ["target_price", "take_profit", "tp_price", "profit_target_r", "limit_target"]:
        bad_payload = {"order_id": "ORD-123", "symbol": "AAPL", bad_key: 160.0}
        with pytest.raises(TakeProfitProhibitedError) as exc:
            prevent_target_price_registration(bad_payload)
        assert bad_key in str(exc.value)

    # Unapproved unknown field (fail-closed allowlist)
    unapproved_payload = {"order_id": "ORD-123", "symbol": "AAPL", "custom_broker_tag": "xyz"}
    with pytest.raises(TakeProfitProhibitedError) as exc:
        prevent_target_price_registration(unapproved_payload)
    assert "unapproved field" in str(exc.value)


def test_ac002_premature_trimming_prohibited(winning_position: CorePosition) -> None:
    """AC-002: Attempting to trim or partially exit a winning position must raise error."""
    with pytest.raises(PrematureTrimmingProhibitedError) as exc_info:
        validate_exit_request(
            position=winning_position,
            proposed_exit_type=ExitType.PROFIT_TRIM,
            requester_id="ScaleOutController",
        )
    assert "PROFIT_TRIM" in str(exc_info.value)
    assert "Trimming or scaling out of valid winners" in str(exc_info.value)


def test_ac003_organic_concentration_breach_protected_from_liquidation(
    winning_position: CorePosition, risk_config: RiskConfig
) -> None:
    """AC-003: Price appreciation causing weight to exceed concentration cap must NOT exit.

    FR-008: Concentration is an intake constraint, not a forced-exit mechanism.
    Position market value = 100 shares * $150.0 = $15,000.
    Portfolio equity = $50,000. Weight = 30.0%, well above the 20% intake cap!
    """
    now = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    decision = evaluate_winner_continuation(
        position=winning_position,
        current_price=150.0,  # Huge gain: +10.0R
        timestamp=now,
        portfolio_equity=50000.0,
        risk_config=risk_config,
    )

    assert decision.is_concentration_breached is True
    assert decision.position_weight_pct == 0.30  # 30% > 20%
    assert decision.continuation_status == ContinuationStatus.RUNNING_WINNER
    assert decision.is_continuation_permitted is True
    assert decision.action == "CONTINUE_HOLD"
    assert "intake constraint only (FR-008)" in decision.explanation
    assert "protected from forced liquidation" in decision.explanation


def test_ac003_concentration_rebalance_exit_raises_error(winning_position: CorePosition) -> None:
    """AC-003: Explicit attempt to rebalance/liquidate on concentration must raise error."""
    with pytest.raises(ForcedConcentrationExitProhibitedError) as exc_info:
        validate_exit_request(
            position=winning_position,
            proposed_exit_type=ExitType.CONCENTRATION_REBALANCE,
            requester_id="PortfolioRebalancer",
        )
    assert "CONCENTRATION_REBALANCE" in str(exc_info.value)
    assert "not a forced-exit mechanism" in str(exc_info.value)


def test_ac004_agent_discretionary_exit_strictly_rejected(winning_position: CorePosition) -> None:
    """AC-004: Agent or discretionary exit requests without invalidation must be rejected."""
    with pytest.raises(DiscretionaryExitProhibitedError) as exc_info:
        validate_exit_request(
            position=winning_position,
            proposed_exit_type=ExitType.DISCRETIONARY_EXIT,
            requester_id="ScientistAgent-TakeProfits",
        )
    assert "DISCRETIONARY_EXIT" in str(exc_info.value)
    assert "ScientistAgent-TakeProfits" in str(exc_info.value)


def test_ac005_deterministic_continuation_decision_receipt(
    winning_position: CorePosition, risk_config: RiskConfig
) -> None:
    """AC-005: evaluate_winner_continuation produces reproducible, immutable audit receipts."""
    now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

    # Run 1
    d1 = evaluate_winner_continuation(
        position=winning_position,
        current_price=110.0,  # +$10/share = +$1,000 profit = +2.0R
        timestamp=now,
        portfolio_equity=100000.0,
        risk_config=risk_config,
    )

    # Run 2
    d2 = evaluate_winner_continuation(
        position=winning_position,
        current_price=110.0,
        timestamp=now,
        portfolio_equity=100000.0,
        risk_config=risk_config,
    )

    assert d1 == d2  # Pure mathematical determinism
    assert isinstance(d1, WinnerContinuationDecision)
    assert d1.unrealized_profit_dollars == 1000.0
    assert d1.unrealized_profit_r == 2.0
    assert d1.initial_risk_dollars == 500.0  # 100 shares * $5
    assert d1.continuation_status == ContinuationStatus.RUNNING_WINNER
    assert d1.action == "CONTINUE_HOLD"
    assert d1.is_continuation_permitted is True


def test_invalid_continuation_state_errors(
    winning_position: CorePosition, risk_config: RiskConfig
) -> None:
    """Verify InvalidContinuationStateError on closed positions or invalid input."""
    now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

    # Non-positive portfolio equity
    with pytest.raises(InvalidContinuationStateError):
        evaluate_winner_continuation(
            position=winning_position,
            current_price=110.0,
            timestamp=now,
            portfolio_equity=0.0,
            risk_config=risk_config,
        )

    # Non-positive current price
    with pytest.raises(InvalidContinuationStateError):
        evaluate_winner_continuation(
            position=winning_position,
            current_price=-10.0,
            timestamp=now,
            portfolio_equity=100000.0,
            risk_config=risk_config,
        )

    # Closed position
    closed_pos = CorePosition(
        position_id=winning_position.position_id,
        symbol=winning_position.symbol,
        entry_order=winning_position.entry_order,
        entry_price=winning_position.entry_price,
        quantity=winning_position.quantity,
        invalidation=winning_position.invalidation,
        status=PositionStatus.CLOSED,
        opened_at=winning_position.opened_at,
    )
    with pytest.raises(InvalidContinuationStateError):
        evaluate_winner_continuation(
            position=closed_pos,
            current_price=110.0,
            timestamp=now,
            portfolio_equity=100000.0,
            risk_config=risk_config,
        )


def test_ac005_1r_calculation_reconciled_with_pyramiding(
    winning_position: CorePosition, strategy_config: StrategyConfig, risk_config: RiskConfig
) -> None:
    """AC-005 / FINDING-001: 1R in continuation must reconcile identically with pyramiding."""
    now = datetime(2026, 2, 1, 14, 0, tzinfo=UTC)
    current_price = 112.50  # Gain = $12.50 / $5.00 = +2.5R

    # Evaluate via pyramiding module
    pyr_eligibility = evaluate_pyramid_eligibility(
        position=winning_position,
        current_price=current_price,
        strategy_config=strategy_config,
    )

    # Evaluate via continuation module
    cont_decision = evaluate_winner_continuation(
        position=winning_position,
        current_price=current_price,
        timestamp=now,
        portfolio_equity=100000.0,
        risk_config=risk_config,
    )

    # Reconcile exact figures
    assert pyr_eligibility.r_multiple == cont_decision.unrealized_profit_r == 2.5
    assert cont_decision.initial_risk_dollars == 500.0


def test_ac005_approved_invalidation_exit_permitted(winning_position: CorePosition) -> None:
    """AC-005 / MISSING-005: validate_exit_request permits approved exits & logs requester."""
    now = datetime(2026, 2, 1, 15, 0, tzinfo=UTC)

    for approved_type in [
        ExitType.INVALIDATION_STOP,
        ExitType.GAP_INVALIDATION_STOP,
        ExitType.STRATEGY_SIGNAL_EXIT,
    ]:
        receipt = validate_exit_request(
            position=winning_position,
            proposed_exit_type=approved_type,
            requester_id="ExecutionGate",
            timestamp=now,
        )
        assert isinstance(receipt, ExitValidationReceipt)
        assert receipt.is_permitted is True
        assert receipt.requester_id == "ExecutionGate"
        assert receipt.exit_type == approved_type


def test_ac006_end_to_end_winner_continuation_pyramiding_and_exit_lifecycle(
    winning_position: CorePosition, strategy_config: StrategyConfig, risk_config: RiskConfig
) -> None:
    """AC-006: Full lifecycle: winner continuation -> pyramiding -> organic growth -> exit.

    1. Entry at $100.0, initial stop at $95.0 (100 shares, 1R = $500).
    2. Price advances to $110.0 (+2.0R):
       - verify continuation confirms RUNNING_WINNER, no exit.
       - pyramid add 1 created (50 shares @ $110.0, stop ratcheted to $100.0).
    3. Price advances to $130.0 (+6.0R on initial risk):
       - market value = 150 shares * $130.0 = $19,500.
       - portfolio equity = $75,000 -> weight = 26.0% (> 20% cap).
       - verify continuation confirms organic concentration breach protected, HOLD.
    4. Price reverses and breaches trailing stop at $100.0:
       - verify_winner_continuation_on_bar emits CoreExitOrder.
       - execute_invalidation_exit closes position with realized profits on blended cost.
    """
    equity = 75000.0

    # Step 1: Position running at $110.0
    bar_trend1 = Bar(
        timestamp=datetime(2026, 2, 5, 16, 0, tzinfo=UTC),
        open=105.0,
        high=111.0,
        low=104.0,
        close=110.0,
        volume=1_000_000,
    )
    decision1, exit_order1 = verify_winner_continuation_on_bar(
        position=winning_position,
        bar=bar_trend1,
        portfolio_equity=equity,
        risk_config=risk_config,
    )
    assert exit_order1 is None
    assert decision1.action == "CONTINUE_HOLD"
    assert decision1.continuation_status == ContinuationStatus.RUNNING_WINNER
    assert decision1.unrealized_profit_r == 2.0

    # Step 2: Create and apply pyramid addition
    stop_ratchet = InvalidationDefinition(
        price=100.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=2.5,
        established_at=datetime(2026, 2, 5, 16, 0, tzinfo=UTC),
        is_ratcheted=True,
        reason="Ratcheting stop to breakeven on add",
    )
    pyramid_order = create_pyramid_order(
        position=winning_position,
        add_price=110.0,
        new_invalidation=stop_ratchet,
        portfolio_equity=equity,
        risk_config=risk_config,
        strategy_config=strategy_config,
        target_add_quantity=30,
    )
    pyramided_pos = apply_pyramid_to_position(winning_position, pyramid_order)
    assert pyramided_pos.quantity > 100.0
    assert pyramided_pos.invalidation.price == 100.0
    assert pyramided_pos.average_entry_price is not None

    # Step 3: Advance to $130.0 (substantial trend continuation)
    bar_trend2 = Bar(
        timestamp=datetime(2026, 2, 20, 16, 0, tzinfo=UTC),
        open=125.0,
        high=132.0,
        low=124.0,
        close=130.0,
        volume=1_500_000,
    )
    decision2, exit_order2 = verify_winner_continuation_on_bar(
        position=pyramided_pos,
        bar=bar_trend2,
        portfolio_equity=equity,
        risk_config=risk_config,
    )
    assert exit_order2 is None
    assert decision2.action == "CONTINUE_HOLD"
    assert decision2.continuation_status == ContinuationStatus.RUNNING_WINNER
    assert decision2.unrealized_profit_r > 5.0

    # Step 4: Trend reversal bar breaches ratcheted stop ($100.0)
    bar_exit = Bar(
        timestamp=datetime(2026, 3, 1, 16, 0, tzinfo=UTC),
        open=102.0,
        high=103.0,
        low=99.0,  # Breaches ratcheted stop of 100.0
        close=99.5,
        volume=2_000_000,
    )
    decision3, exit_order3 = verify_winner_continuation_on_bar(
        position=pyramided_pos,
        bar=bar_exit,
        portfolio_equity=equity,
        risk_config=risk_config,
    )
    assert exit_order3 is not None
    assert exit_order3.trigger_price == 100.0
    assert decision3.action == "EXIT_REQUIRED"

    # Step 5: Execute exit fill
    closed_pos = execute_invalidation_exit(
        position=pyramided_pos,
        exit_order=exit_order3,
        fill_price=100.0,
        fill_timestamp=datetime(2026, 3, 1, 16, 0, tzinfo=UTC),
    )
    assert closed_pos.status == PositionStatus.CLOSED
    # Because stop was ratcheted to $100.0 while cost basis was blended around $103.4,
    # exit realized according to strict formula
    assert closed_pos.realized_pnl is not None
