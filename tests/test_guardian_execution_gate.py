"""Tests for TOSO Guardian Execution Gate (Sole Authority) (GUARD-004).

Verifies:
- FR-012: Every executable order passes through the deterministic Execution Gate.
- FR-012 §1, §2, §3: Evaluation across all three required domains:
  1. System & Feed Check
  2. Intake & Risk Check
  3. Execution Safety Check
- INV-007: One Execution Authority — no trading order may bypass the Gate.
- INV-008: UNKNOWN Is Not Safe — missing, non-positive, or stale inputs fail closed.
- INV-001, INV-006: Pure deterministic evaluation, clock anchor echoing, immutable receipts.
- AC-001, AC-002, AC-003, AC-004.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from toso.config.models import CapitalAllocationConfig, RiskConfig
from toso.guardian.errors import (
    ExecutionSafetyCheckFailedError,
    IntakeRiskCheckFailedError,
    SystemFeedCheckFailedError,
    UnknownGateInputError,
)
from toso.guardian.gate import (
    enforce_execution_gate,
    evaluate_execution_gate,
    submit_through_gate,
)
from toso.guardian.models import (
    ExecutionSafetyContext,
    GateDecisionStatus,
    GateDomain,
    PortfolioStateSnapshot,
    SystemFeedContext,
)


@pytest.fixture
def eval_timestamp() -> datetime:
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def healthy_feed_context(eval_timestamp: datetime) -> SystemFeedContext:
    return SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=eval_timestamp - timedelta(seconds=10),
        evaluation_timestamp=eval_timestamp,
        max_feed_staleness_seconds=60.0,
    )


@pytest.fixture
def default_risk_config() -> RiskConfig:
    return RiskConfig()


@pytest.fixture
def default_capital_config() -> CapitalAllocationConfig:
    return CapitalAllocationConfig()


@pytest.fixture
def sample_portfolio_state(eval_timestamp: datetime) -> PortfolioStateSnapshot:
    return PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        timestamp=eval_timestamp,
    )


def test_ac001_and_ac004_three_domains_evaluated_and_approved(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    healthy_feed_context: SystemFeedContext,
) -> None:
    """Lens: Boundary & Traceability (AC-001, AC-004).

    Valid order evaluated across System & Feed, Intake & Risk, and Execution Safety.
    All 3 domains pass, receipt is APPROVED.
    """
    receipt = evaluate_execution_gate(
        order_id="ORD-101",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
    )

    assert receipt.is_approved is True
    assert receipt.status == GateDecisionStatus.APPROVED
    assert receipt.order_id == "ORD-101"
    assert receipt.symbol == "AAPL"
    assert receipt.rejection_reason is None

    # Verify all 3 functional domains are evaluated
    evaluated_domains = {check.domain for check in receipt.check_results}
    assert GateDomain.SYSTEM_FEED in evaluated_domains
    assert GateDomain.INTAKE_RISK in evaluated_domains
    assert GateDomain.EXECUTION_SAFETY in evaluated_domains

    # Check that individual receipts are embedded
    assert receipt.portfolio_risk_assessment is not None
    assert receipt.concentration_assessment is not None
    assert receipt.capital_allocation_assessment is not None


def test_ac002_fail_closed_unknown_inputs(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    healthy_feed_context: SystemFeedContext,
) -> None:
    """Lens: QA Edge & Fail-Closed (INV-008, AC-002).

    Missing, empty, non-positive, or NaN inputs must fail closed with UnknownGateInputError.
    """
    # Empty order ID
    with pytest.raises(UnknownGateInputError, match="order_id"):
        evaluate_execution_gate(
            order_id="",
            symbol="AAPL",
            shares=100,
            entry_price=150.0,
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )

    # Empty symbol
    with pytest.raises(UnknownGateInputError, match="symbol"):
        evaluate_execution_gate(
            order_id="ORD-102",
            symbol="   ",
            shares=100,
            entry_price=150.0,
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )

    # Non-positive shares
    with pytest.raises(UnknownGateInputError, match="shares"):
        evaluate_execution_gate(
            order_id="ORD-103",
            symbol="AAPL",
            shares=0,
            entry_price=150.0,
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )

    # NaN price
    with pytest.raises(UnknownGateInputError, match="entry_price"):
        evaluate_execution_gate(
            order_id="ORD-104",
            symbol="AAPL",
            shares=100,
            entry_price=float("nan"),
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )


def test_ac003_rejected_gate_cannot_reach_broker_dispatch(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    eval_timestamp: datetime,
) -> None:
    """Lens: Security & Sole Execution Authority (INV-007, AC-003).

    A rejected Gate decision can NEVER reach broker dispatch.
    """
    mock_dispatch = Mock()
    stale_context = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=eval_timestamp - timedelta(seconds=120),  # Stale > 60s
        evaluation_timestamp=eval_timestamp,
        max_feed_staleness_seconds=60.0,
    )

    dummy_order = {"order_id": "ORD-BROKER-TEST", "shares": 100}

    # 1. Non-raising invocation returns rejection receipt and None dispatch result
    receipt, result = submit_through_gate(
        order=dummy_order,
        dispatch_fn=mock_dispatch,
        order_id="ORD-BROKER-TEST",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=stale_context,
        raise_on_rejection=False,
    )

    assert receipt.is_approved is False
    assert result is None
    # CRITICAL: Verify dispatch_fn was never called
    assert mock_dispatch.call_count == 0

    # 2. Raising invocation raises domain error and still never calls dispatch_fn
    with pytest.raises(SystemFeedCheckFailedError):
        submit_through_gate(
            order=dummy_order,
            dispatch_fn=mock_dispatch,
            order_id="ORD-BROKER-TEST",
            symbol="AAPL",
            shares=100,
            entry_price=150.0,
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=stale_context,
            raise_on_rejection=True,
        )

    assert mock_dispatch.call_count == 0

    # 3. Successful invocation calls dispatch_fn exactly once
    good_context = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=eval_timestamp - timedelta(seconds=5),
        evaluation_timestamp=eval_timestamp,
        max_feed_staleness_seconds=60.0,
    )
    mock_dispatch.return_value = {"status": "SUBMITTED"}

    good_receipt, good_result = submit_through_gate(
        order=dummy_order,
        dispatch_fn=mock_dispatch,
        order_id="ORD-BROKER-TEST-2",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=good_context,
    )

    assert good_receipt.is_approved is True
    assert good_result == {"status": "SUBMITTED"}
    assert mock_dispatch.call_count == 1
    mock_dispatch.assert_called_once_with(dummy_order)


def test_domain_1_system_and_feed_failures(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    eval_timestamp: datetime,
) -> None:
    """Lens: Failure Paths — Domain 1 (System & Feed)."""
    # 1. System not operational
    ctx_sys_down = SystemFeedContext(
        is_system_operational=False,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=eval_timestamp - timedelta(seconds=5),
        evaluation_timestamp=eval_timestamp,
    )
    receipt = evaluate_execution_gate(
        order_id="ORD-D1-1",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=ctx_sys_down,
    )
    assert receipt.is_approved is False
    assert receipt.status == GateDecisionStatus.REJECTED
    with pytest.raises(SystemFeedCheckFailedError, match="System is not operational"):
        enforce_execution_gate(
            order_id="ORD-D1-1",
            symbol="AAPL",
            shares=100,
            entry_price=150.0,
            stop_loss_price=140.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=ctx_sys_down,
        )

    # 2. Broker disconnected -> HELD
    ctx_broker_down = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=False,
        is_emergency_stop_active=False,
        feed_timestamp=eval_timestamp - timedelta(seconds=5),
        evaluation_timestamp=eval_timestamp,
    )
    receipt_broker = evaluate_execution_gate(
        order_id="ORD-D1-2",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=ctx_broker_down,
    )
    assert receipt_broker.is_approved is False
    assert receipt_broker.status == GateDecisionStatus.HELD

    # 3. Emergency stop active
    ctx_em_stop = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=True,
        feed_timestamp=eval_timestamp - timedelta(seconds=5),
        evaluation_timestamp=eval_timestamp,
    )
    receipt_em = evaluate_execution_gate(
        order_id="ORD-D1-3",
        symbol="AAPL",
        shares=100,
        entry_price=150.0,
        stop_loss_price=140.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=ctx_em_stop,
    )
    assert receipt_em.is_approved is False
    assert "Emergency stop is active." in receipt_em.rejection_reasons


def test_domain_2_intake_and_risk_failures(
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    healthy_feed_context: SystemFeedContext,
    eval_timestamp: datetime,
) -> None:
    """Lens: Failure Paths — Domain 2 (Intake & Risk)."""
    # 1. Single trade risk limit exceeded (10% single trade risk vs 1.5% max)
    portfolio_state = PortfolioStateSnapshot(
        portfolio_equity=100_000.0,
        peak_portfolio_equity=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        timestamp=eval_timestamp,
    )

    # 1000 shares * ($100 - $80) = $20,000 risk = 20% of equity (limit 1.5%)
    receipt = evaluate_execution_gate(
        order_id="ORD-D2-1",
        symbol="AAPL",
        shares=1000,
        entry_price=100.0,
        stop_loss_price=80.0,
        portfolio_state=portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
    )
    assert receipt.is_approved is False
    assert receipt.status == GateDecisionStatus.REJECTED
    with pytest.raises(IntakeRiskCheckFailedError):
        enforce_execution_gate(
            order_id="ORD-D2-1",
            symbol="AAPL",
            shares=1000,
            entry_price=100.0,
            stop_loss_price=80.0,
            portfolio_state=portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )


def test_domain_3_execution_safety_failures(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    healthy_feed_context: SystemFeedContext,
) -> None:
    """Lens: Failure Paths & Red Team — Domain 3 (Execution Safety)."""
    # 1. Stop loss not below entry price (missing/inverted protective stop)
    receipt_stop = evaluate_execution_gate(
        order_id="ORD-D3-1",
        symbol="AAPL",
        shares=100,
        entry_price=100.0,
        stop_loss_price=105.0,  # Invalid: stop >= entry
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
    )
    assert receipt_stop.is_approved is False
    with pytest.raises(ExecutionSafetyCheckFailedError, match="Protective stop"):
        enforce_execution_gate(
            order_id="ORD-D3-1",
            symbol="AAPL",
            shares=100,
            entry_price=100.0,
            stop_loss_price=105.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
        )

    # 2. Conflicting order in-flight at broker
    safety_with_inflight = ExecutionSafetyContext(
        active_in_flight_orders=("AAPL", "MSFT"),
    )
    receipt_conflict = evaluate_execution_gate(
        order_id="ORD-D3-2",
        symbol="AAPL",  # In-flight!
        shares=100,
        entry_price=100.0,
        stop_loss_price=95.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
        safety_context=safety_with_inflight,
    )
    assert receipt_conflict.is_approved is False
    with pytest.raises(ExecutionSafetyCheckFailedError, match="already in-flight"):
        enforce_execution_gate(
            order_id="ORD-D3-2",
            symbol="AAPL",
            shares=100,
            entry_price=100.0,
            stop_loss_price=95.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
            safety_context=safety_with_inflight,
        )

    # 3. Share quantity bounds check
    safety_bounds = ExecutionSafetyContext(min_order_shares=10, max_order_shares=50)
    receipt_size = evaluate_execution_gate(
        order_id="ORD-D3-3",
        symbol="AAPL",
        shares=100,  # Exceeds max 50
        entry_price=100.0,
        stop_loss_price=99.0,  # Risk is $100 (0.1%), well within 1.0% risk limit
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
        safety_context=safety_bounds,
    )
    assert receipt_size.is_approved is False
    with pytest.raises(ExecutionSafetyCheckFailedError, match="outside allowable bounds"):
        enforce_execution_gate(
            order_id="ORD-D3-3",
            symbol="AAPL",
            shares=100,
            entry_price=100.0,
            stop_loss_price=99.0,
            portfolio_state=sample_portfolio_state,
            risk_config=default_risk_config,
            capital_config=default_capital_config,
            system_feed_context=healthy_feed_context,
            safety_context=safety_bounds,
        )


def test_determinism_and_immutability(
    sample_portfolio_state: PortfolioStateSnapshot,
    default_risk_config: RiskConfig,
    default_capital_config: CapitalAllocationConfig,
    healthy_feed_context: SystemFeedContext,
) -> None:
    """Lens: Skeptic & Runtime (INV-001, INV-006).

    Repeated runs produce byte-identical JSON receipts; models are frozen.
    """
    res1 = evaluate_execution_gate(
        order_id="ORD-DET-1",
        symbol="AAPL",
        shares=100,
        entry_price=100.0,
        stop_loss_price=95.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
    )
    res2 = evaluate_execution_gate(
        order_id="ORD-DET-1",
        symbol="AAPL",
        shares=100,
        entry_price=100.0,
        stop_loss_price=95.0,
        portfolio_state=sample_portfolio_state,
        risk_config=default_risk_config,
        capital_config=default_capital_config,
        system_feed_context=healthy_feed_context,
    )

    assert res1 == res2
    assert res1.model_dump_json() == res2.model_dump_json()
    assert res1.timestamp == healthy_feed_context.evaluation_timestamp

    # Immutability check
    with pytest.raises(ValidationError):
        res1.is_approved = False  # type: ignore[misc]
