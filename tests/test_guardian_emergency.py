"""Verification Test Suite for Story GUARD-006: Emergency Stop & Fail-Closed Behavior.

Verifies:
- FR-015: Deterministic emergency stop distinct from ordinary pause.
- INV-008: Fail-closed behavior on UNKNOWN, missing, stale, or malformed inputs.
- INV-001: Pure mathematical determinism, zero wall-clock reads.
- INV-006: Deterministic, immutable audit receipt generation.
- FR-012 / GUARD-004: Execution Gate lock synchronization.
- FR-014 / GUARD-005: Protection failure escalation.
"""

from datetime import UTC, datetime

import pytest

from toso.config.models import (
    CapitalAllocationConfig,
    RiskConfig,
)
from toso.guardian.emergency import (
    _validate_float,
    create_locked_gate_context,
    enforce_emergency_stop_clearance,
    evaluate_emergency_state,
)
from toso.guardian.errors import (
    EmergencyStopActiveError,
    ExecutionGateError,
    UnknownEmergencyInputError,
)
from toso.guardian.gate import (
    evaluate_execution_gate,
    submit_through_gate,
)
from toso.guardian.models import (
    CapitalSleeve,
    EmergencyStopMode,
    EmergencyTriggerType,
    ExecutionSafetyContext,
    LiquidationScope,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProtectionContract,
    ProtectionStatus,
    SystemFeedContext,
)


@pytest.fixture
def default_risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_portfolio_risk_pct=0.06,
        max_aggregate_position_risk_pct=0.03,
        emergency_drawdown_stop_pct=0.15,
        max_position_concentration_pct=0.20,
    )


@pytest.fixture
def default_capital_config() -> CapitalAllocationConfig:
    return CapitalAllocationConfig(
        core_allocation_pct=0.70,
        reserve_allocation_pct=0.20,
        black_swan_allocation_pct=0.10,
    )


@pytest.fixture
def sample_positions() -> list[PositionRiskSnapshot]:
    return [
        PositionRiskSnapshot(
            symbol="AAPL",
            shares=100,
            current_price=160.0,
            stop_loss_price=145.0,
            open_risk_dollars=500.0,
            market_value=16000.0,
            sleeve=CapitalSleeve.CORE,
        ),
        PositionRiskSnapshot(
            symbol="MSFT",
            shares=50,
            current_price=310.0,
            stop_loss_price=290.0,
            open_risk_dollars=500.0,
            market_value=15500.0,
            sleeve=CapitalSleeve.CORE,
        ),
        PositionRiskSnapshot(
            symbol="SH",  # Short S&P hedge in Black Swan sleeve
            shares=200,
            current_price=15.0,
            stop_loss_price=14.0,
            open_risk_dollars=200.0,
            market_value=3000.0,
            sleeve=CapitalSleeve.BLACK_SWAN,
        ),
    ]


@pytest.fixture
def base_portfolio_state(sample_positions: list[PositionRiskSnapshot]) -> PortfolioStateSnapshot:
    return PortfolioStateSnapshot(
        portfolio_equity=100000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=65500.0,
        positions=sample_positions,
        timestamp=datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def base_system_feed_context() -> SystemFeedContext:
    ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    return SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        evaluation_timestamp=ts,
        feed_timestamp=ts,
        max_feed_staleness_seconds=60.0,
    )


def test_ac001_pause_vs_emergency_stop_distinction(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify FR-015: Emergency Stop is distinct from ordinary Pause.

    A Pause prevents new exposure while continuing normal management of existing positions.
    Emergency Stop terminates autonomous trading and initiates emergency safety liquidation.
    """
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    pending_orders = ["ORD-BUY-001", "ORD-BUY-002"]

    # 1. Evaluate PAUSE
    pause_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_pause=True,
        system_feed_context=base_system_feed_context,
        pending_order_ids=pending_orders,
    )

    assert pause_receipt.current_mode == EmergencyStopMode.PAUSED
    assert pause_receipt.is_gate_locked is True  # Gate blocks new entries/pyramids
    assert pause_receipt.is_liquidation_required is False  # Does NOT liquidate existing positions!
    assert len(pause_receipt.liquidation_orders) == 0  # Positions continue normal management
    assert set(pause_receipt.orders_to_cancel) == set(
        pending_orders
    )  # Pending intake buys canceled

    # 2. Evaluate EMERGENCY STOP
    emergency_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
        pending_order_ids=pending_orders,
    )

    assert emergency_receipt.current_mode == EmergencyStopMode.LIQUIDATING
    assert emergency_receipt.is_gate_locked is True
    assert emergency_receipt.is_liquidation_required is True  # DOES initiate safety liquidation!
    assert len(emergency_receipt.liquidation_orders) == len(base_portfolio_state.positions)
    assert set(emergency_receipt.orders_to_cancel) == set(pending_orders)

    # Check exact share matching on liquidation orders
    for liq_order in emergency_receipt.liquidation_orders:
        matched_pos = next(
            p for p in base_portfolio_state.positions if p.symbol == liq_order.symbol
        )
        assert liq_order.shares == matched_pos.shares
        assert liq_order.side == "SELL"
        assert liq_order.order_type == "MARKET"


def test_ac002_drawdown_breach_triggers_emergency_halt(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify FR-015: Portfolio drawdown reaching or breaching threshold triggers emergency stop."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    # Peak = 100,000; Equity = 84,000 -> Drawdown = 16.0% (Threshold = 15.0%)
    breach_state = PortfolioStateSnapshot(
        portfolio_equity=84000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=50000.0,
        positions=base_portfolio_state.positions,
        timestamp=eval_ts,
    )

    receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=breach_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
    )

    assert receipt.current_mode == EmergencyStopMode.LIQUIDATING
    assert receipt.trigger_type == EmergencyTriggerType.PORTFOLIO_DRAWDOWN_BREACH
    assert receipt.is_gate_locked is True
    assert receipt.is_liquidation_required is True
    assert any("drawdown 16.00% reaches or breaches" in r for r in receipt.trigger_reasons)

    # Boundary check: Drawdown exactly at 15.0% (Peak=100,000, Equity=85,000)
    boundary_state = PortfolioStateSnapshot(
        portfolio_equity=85000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=50000.0,
        positions=base_portfolio_state.positions,
        timestamp=eval_ts,
    )
    boundary_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=boundary_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
    )
    assert boundary_receipt.current_mode == EmergencyStopMode.LIQUIDATING
    assert boundary_receipt.trigger_type == EmergencyTriggerType.PORTFOLIO_DRAWDOWN_BREACH

    # Below boundary: Drawdown at 10.0% (Equity=90,000) -> NORMAL
    normal_state = PortfolioStateSnapshot(
        portfolio_equity=90000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=50000.0,
        positions=base_portfolio_state.positions,
        timestamp=eval_ts,
    )
    normal_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=normal_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
    )
    assert normal_receipt.current_mode == EmergencyStopMode.NORMAL
    assert normal_receipt.is_gate_locked is False
    assert normal_receipt.is_liquidation_required is False


def test_ac003_system_feed_outage_triggers_emergency_halt(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
) -> None:
    """Verify FR-015: Critical system/feed outages trigger emergency halt."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    # 1. System not operational
    ctx_unoperational = SystemFeedContext(
        is_system_operational=False,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        evaluation_timestamp=eval_ts,
        feed_timestamp=eval_ts,
        max_feed_staleness_seconds=60.0,
    )
    receipt1 = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=ctx_unoperational,
    )
    assert receipt1.current_mode == EmergencyStopMode.LIQUIDATING
    assert receipt1.trigger_type == EmergencyTriggerType.SYSTEM_FEED_OUTAGE

    # 2. Broker disconnected
    ctx_broker_down = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=False,
        is_emergency_stop_active=False,
        evaluation_timestamp=eval_ts,
        feed_timestamp=eval_ts,
        max_feed_staleness_seconds=60.0,
    )
    receipt2 = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=ctx_broker_down,
    )
    assert receipt2.current_mode == EmergencyStopMode.LIQUIDATING
    assert receipt2.trigger_type == EmergencyTriggerType.SYSTEM_FEED_OUTAGE

    # 3. Market data feed stale
    feed_stale_ts = datetime(2026, 10, 22, 11, 55, 0, tzinfo=UTC)  # 5 minutes stale > 60s
    ctx_stale = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        evaluation_timestamp=eval_ts,
        feed_timestamp=feed_stale_ts,
        max_feed_staleness_seconds=60.0,
    )
    receipt3 = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=ctx_stale,
    )
    assert receipt3.current_mode == EmergencyStopMode.LIQUIDATING
    assert receipt3.trigger_type == EmergencyTriggerType.SYSTEM_FEED_OUTAGE


def test_ac004_protection_recovery_failure_escalates_to_emergency_halt(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify FR-014 §3 / FR-015: Protection recovery failure escalates to emergency stop."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    contracts = [
        ProtectionContract(
            position_id="POS-AAPL",
            symbol="AAPL",
            shares=100,
            status=ProtectionStatus.RECOVERY_FAILED,  # Unrecoverable stop failure
            active_stop_order_id=None,
            active_stop_price=None,
            invalidation_price=145.0,
            recovery_attempts=2,
            max_recovery_attempts=2,
            is_verified=False,
            last_verified_at=eval_ts,
        )
    ]

    receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
        protection_contracts=contracts,
    )

    assert receipt.current_mode == EmergencyStopMode.LIQUIDATING
    assert receipt.trigger_type == EmergencyTriggerType.PROTECTION_FAILURE
    assert receipt.is_gate_locked is True
    assert receipt.is_liquidation_required is True


def test_ac005_selective_sleeve_vs_full_portfolio_liquidation(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify selective sleeve liquidation (CORE_ONLY) vs full portfolio liquidation."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    # 1. CORE_ONLY scope
    core_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
        liquidation_scope=LiquidationScope.CORE_ONLY,
    )

    liquidated_symbols = [o.symbol for o in core_receipt.liquidation_orders]
    assert "AAPL" in liquidated_symbols
    assert "MSFT" in liquidated_symbols
    assert "SH" not in liquidated_symbols  # Black Swan hedge preserved!

    # 2. ALL_POSITIONS scope
    all_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
        liquidation_scope=LiquidationScope.ALL_POSITIONS,
    )

    all_symbols = [o.symbol for o in all_receipt.liquidation_orders]
    assert "AAPL" in all_symbols
    assert "MSFT" in all_symbols
    assert "SH" in all_symbols  # All liquidated


def test_ac006_terminal_halted_state_when_portfolio_flat(
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify transition from LIQUIDATING to terminal HALTED when all positions are closed."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    flat_portfolio = PortfolioStateSnapshot(
        portfolio_equity=75000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=75000.0,
        positions=(),  # Flat portfolio
        timestamp=eval_ts,
    )

    receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.LIQUIDATING,
        portfolio_state=flat_portfolio,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
    )

    assert receipt.current_mode == EmergencyStopMode.HALTED
    assert receipt.is_gate_locked is True
    assert receipt.is_liquidation_required is False
    assert len(receipt.liquidation_orders) == 0


def test_ac007_gate_locking_integration_and_enforcement(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify Execution Gate synchronization and enforcement under emergency stop."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    # 1. Generate active emergency receipt
    emergency_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
    )

    # 2. Derive locked Execution Gate context
    locked_context = create_locked_gate_context(emergency_receipt, base_system_feed_context)
    assert locked_context.is_emergency_stop_active is True

    # 3. Verify evaluate_execution_gate rejects order
    gate_receipt = evaluate_execution_gate(
        order_id="ORD-INTAKE-999",
        symbol="GOOGL",
        shares=10,
        entry_price=100.0,
        stop_loss_price=95.0,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=locked_context,
        safety_context=ExecutionSafetyContext(),
    )
    assert gate_receipt.is_approved is False
    assert any("Emergency stop is active" in r for r in gate_receipt.rejection_reasons)

    # 4. Verify submit_through_gate raises ExecutionGateError when raise_on_rejection=True
    dispatched = False

    def mock_broker_submit(order_obj: dict) -> str:
        nonlocal dispatched
        dispatched = True
        return "BROKER-ID-123"

    with pytest.raises(ExecutionGateError):
        submit_through_gate(
            order={"symbol": "GOOGL", "shares": 10},
            dispatch_fn=mock_broker_submit,
            order_id="ORD-INTAKE-999",
            symbol="GOOGL",
            shares=10,
            entry_price=100.0,
            stop_loss_price=95.0,
            portfolio_state=base_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=locked_context,
            raise_on_rejection=True,
        )
    assert dispatched is False

    # 5. Verify enforce_emergency_stop_clearance
    with pytest.raises(EmergencyStopActiveError):
        enforce_emergency_stop_clearance(emergency_receipt)

    normal_receipt = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        system_feed_context=base_system_feed_context,
    )
    enforce_emergency_stop_clearance(normal_receipt)  # Should not raise


def test_ac008_fail_closed_on_corrupt_unknown_inputs(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify INV-008: Corrupted or UNKNOWN inputs fail closed with UnknownEmergencyInputError."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    # 1. Direct validation: NaN, infinite, or non-numeric float values
    with pytest.raises(UnknownEmergencyInputError):
        _validate_float(float("nan"), "test_nan")

    with pytest.raises(UnknownEmergencyInputError):
        _validate_float(float("inf"), "test_inf")

    with pytest.raises(UnknownEmergencyInputError):
        _validate_float(-10.0, "test_negative", must_be_positive=True)

    with pytest.raises(UnknownEmergencyInputError):
        _validate_float("not-a-number", "test_str")  # type: ignore

    # 2. Equity > Peak equity
    inverted_equity_state = PortfolioStateSnapshot(
        portfolio_equity=120000.0,
        peak_portfolio_equity=100000.0,
        cash_balance=50000.0,
        positions=base_portfolio_state.positions,
        timestamp=eval_ts,
    )
    with pytest.raises(UnknownEmergencyInputError):
        evaluate_emergency_state(
            current_mode=EmergencyStopMode.NORMAL,
            portfolio_state=inverted_equity_state,
            risk_config=default_risk_config,
            evaluation_timestamp=eval_ts,
        )

    # 3. Non-datetime timestamp
    with pytest.raises(UnknownEmergencyInputError):
        evaluate_emergency_state(
            current_mode=EmergencyStopMode.NORMAL,
            portfolio_state=base_portfolio_state,
            risk_config=default_risk_config,
            evaluation_timestamp="invalid-timestamp",  # type: ignore
        )

    # 4. Invalid current_mode
    with pytest.raises(UnknownEmergencyInputError):
        evaluate_emergency_state(
            current_mode="INVALID_MODE",  # type: ignore
            portfolio_state=base_portfolio_state,
            risk_config=default_risk_config,
            evaluation_timestamp=eval_ts,
        )


def test_ac009_pure_determinism_and_immutable_receipts(
    base_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    base_system_feed_context: SystemFeedContext,
) -> None:
    """Verify INV-001 & INV-006: Identical inputs produce identical receipts; immutable models."""
    eval_ts = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

    receipt1 = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
    )

    receipt2 = evaluate_emergency_state(
        current_mode=EmergencyStopMode.NORMAL,
        portfolio_state=base_portfolio_state,
        risk_config=default_risk_config,
        evaluation_timestamp=eval_ts,
        operator_emergency_halt=True,
        system_feed_context=base_system_feed_context,
    )

    assert receipt1 == receipt2
    assert receipt1.receipt_id == receipt2.receipt_id

    # Immutability check
    with pytest.raises(Exception):
        receipt1.current_mode = EmergencyStopMode.NORMAL  # type: ignore

    if receipt1.liquidation_orders:
        with pytest.raises(Exception):
            receipt1.liquidation_orders[0].shares = 999  # type: ignore
