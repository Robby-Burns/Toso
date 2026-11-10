"""TOSO Guardian Emergency Stop & Fail-Closed Engine.

Implements FR-015, INV-008, INV-001, INV-006:
- Deterministic emergency mechanism distinct from ordinary pause:
  * PAUSE: halts new intake/exposure while allowing normal management of existing positions.
  * EMERGENCY STOP: terminates autonomous trading, cancels pending intake orders,
    and initiates deterministic safety liquidation.
- Clear trigger evaluation hierarchy:
  1. Fail-closed on UNKNOWN/invalid input.
  2. Manual operator emergency halt.
  3. Peak-to-trough portfolio drawdown circuit breaker.
  4. System & feed outage / broker disconnection.
  5. Protection recovery failure escalation.
  6. Manual operator pause.
- Selective vs. full portfolio liquidation scoping.
- Execution Gate lock synchronization (`is_emergency_stop_active=True`).
- Pure determinism and immutable audit receipts with zero wall-clock reads.
"""

import math
from collections.abc import Sequence
from datetime import datetime

from toso.config.models import RiskConfig
from toso.guardian.errors import (
    EmergencyStopActiveError,
    UnknownEmergencyInputError,
)
from toso.guardian.evaluator import calculate_portfolio_drawdown
from toso.guardian.models import (
    CapitalSleeve,
    EmergencyActionReceipt,
    EmergencyLiquidationOrder,
    EmergencyStopMode,
    EmergencyTriggerType,
    LiquidationScope,
    PortfolioStateSnapshot,
    ProtectionContract,
    ProtectionStatus,
    SystemFeedContext,
)

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed handling on corrupt or UNKNOWN inputs (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownEmergencyInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownEmergencyInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownEmergencyInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownEmergencyInputError(f"Field '{name}' cannot be negative, got {val}")


def evaluate_emergency_state(
    current_mode: EmergencyStopMode,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    evaluation_timestamp: datetime,
    operator_emergency_halt: bool = False,
    operator_pause: bool = False,
    system_feed_context: SystemFeedContext | None = None,
    protection_contracts: Sequence[ProtectionContract] = (),
    pending_order_ids: Sequence[str] = (),
    liquidation_scope: LiquidationScope = LiquidationScope.ALL_POSITIONS,
    specific_symbols: Sequence[str] = (),
    receipt_id: str | None = None,
) -> EmergencyActionReceipt:
    """Deterministically evaluate system emergency state and generate safety actions (FR-015).

    Args:
        current_mode: Operational mode prior to this evaluation.
        portfolio_state: Authoritative portfolio snapshot.
        risk_config: Active risk configuration holding drawdown thresholds.
        evaluation_timestamp: Pure clock anchor.
        operator_emergency_halt: True if an explicit manual emergency halt is requested.
        operator_pause: True if an explicit manual intake pause is requested.
        system_feed_context: Optional feed and system operational context.
        protection_contracts: Active protection contracts for open positions.
        pending_order_ids: IDs of open pending orders (intake/buys).
        liquidation_scope: Target scope for safety liquidation.
        specific_symbols: Symbols targeted when liquidation_scope is SPECIFIC_SYMBOLS.
        receipt_id: Deterministic identifier override; generated deterministically if None.

    Returns:
        Immutable EmergencyActionReceipt with evaluation status, cancels, and liquidation orders.

    Raises:
        UnknownEmergencyInputError: If any input field is invalid, NaN, infinite, or missing.
    """
    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownEmergencyInputError(
            f"evaluation_timestamp must be a datetime, got {evaluation_timestamp}"
        )
    if not isinstance(current_mode, EmergencyStopMode):
        raise UnknownEmergencyInputError(
            f"current_mode must be an EmergencyStopMode, got {current_mode}"
        )

    # 1. Validate inputs for strict fail-closed handling (INV-008)
    _validate_float(portfolio_state.portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(
        portfolio_state.peak_portfolio_equity, "peak_portfolio_equity", must_be_positive=True
    )
    _validate_float(
        risk_config.emergency_drawdown_stop_pct,
        "emergency_drawdown_stop_pct",
        must_be_positive=True,
    )

    if portfolio_state.portfolio_equity > portfolio_state.peak_portfolio_equity:
        raise UnknownEmergencyInputError(
            f"portfolio_equity ({portfolio_state.portfolio_equity}) cannot exceed "
            f"peak_portfolio_equity ({portfolio_state.peak_portfolio_equity})"
        )

    for pos in portfolio_state.positions:
        if not pos.symbol or not isinstance(pos.symbol, str):
            raise UnknownEmergencyInputError("Position symbol must be a non-empty string.")
        if not isinstance(pos.shares, int) or pos.shares <= 0:
            raise UnknownEmergencyInputError(
                f"Position {pos.symbol} shares must be positive int, got {pos.shares}."
            )
        _validate_float(
            pos.market_value, f"position({pos.symbol}).market_value", must_be_positive=True
        )

    # 2. Trigger Evaluation Hierarchy
    trigger_type: EmergencyTriggerType | None = None
    trigger_reasons: list[str] = []
    target_mode: EmergencyStopMode = current_mode

    # Trigger Priority 1: Manual Operator Emergency Halt
    if operator_emergency_halt:
        trigger_type = EmergencyTriggerType.MANUAL_OPERATOR
        trigger_reasons.append("Manual operator emergency halt requested.")
        target_mode = EmergencyStopMode.EMERGENCY_HALT

    # Trigger Priority 2: Peak-to-Trough Portfolio Drawdown Circuit Breaker
    current_drawdown = calculate_portfolio_drawdown(
        portfolio_state.portfolio_equity, portfolio_state.peak_portfolio_equity
    )
    if current_drawdown >= risk_config.emergency_drawdown_stop_pct - EPSILON:
        if trigger_type is None:
            trigger_type = EmergencyTriggerType.PORTFOLIO_DRAWDOWN_BREACH
        trigger_reasons.append(
            f"Portfolio drawdown {current_drawdown:.2%} reaches or breaches "
            f"emergency stop threshold {risk_config.emergency_drawdown_stop_pct:.2%}."
        )
        target_mode = EmergencyStopMode.EMERGENCY_HALT

    # Trigger Priority 3: System & Feed Outage / Broker Disconnection
    if system_feed_context is not None:
        if not system_feed_context.is_system_operational:
            if trigger_type is None:
                trigger_type = EmergencyTriggerType.SYSTEM_FEED_OUTAGE
            trigger_reasons.append("System is not in an operational state.")
            target_mode = EmergencyStopMode.EMERGENCY_HALT

        if not system_feed_context.is_broker_connected:
            if trigger_type is None:
                trigger_type = EmergencyTriggerType.SYSTEM_FEED_OUTAGE
            trigger_reasons.append("Broker connection is offline or disconnected.")
            target_mode = EmergencyStopMode.EMERGENCY_HALT

        staleness = (evaluation_timestamp - system_feed_context.feed_timestamp).total_seconds()
        if staleness > system_feed_context.max_feed_staleness_seconds:
            if trigger_type is None:
                trigger_type = EmergencyTriggerType.SYSTEM_FEED_OUTAGE
            trigger_reasons.append(
                f"Market feed is critically stale ({staleness:.1f}s > "
                f"{system_feed_context.max_feed_staleness_seconds:.1f}s)."
            )
            target_mode = EmergencyStopMode.EMERGENCY_HALT

    # Trigger Priority 4: Protection Recovery Failure Escalation (FR-014 §3)
    critical_protection_failures = [
        c
        for c in protection_contracts
        if c.status in (ProtectionStatus.RECOVERY_FAILED, ProtectionStatus.LIQUIDATING)
    ]
    if critical_protection_failures:
        if trigger_type is None:
            trigger_type = EmergencyTriggerType.PROTECTION_FAILURE
        for failed_contract in critical_protection_failures:
            trigger_reasons.append(
                f"Position {failed_contract.symbol} protection recovery failed "
                f"(status={failed_contract.status.value})."
            )
        target_mode = EmergencyStopMode.EMERGENCY_HALT

    # Trigger Priority 5: Manual Operator Pause (distinct from emergency stop: no liquidation)
    if operator_pause and target_mode not in (
        EmergencyStopMode.EMERGENCY_HALT,
        EmergencyStopMode.LIQUIDATING,
        EmergencyStopMode.HALTED,
    ):
        if trigger_type is None:
            trigger_type = EmergencyTriggerType.MANUAL_OPERATOR
        trigger_reasons.append("Manual operator intake pause requested.")
        target_mode = EmergencyStopMode.PAUSED

    # State Resolution for In-Progress Liquidation or Terminal Halted State
    has_open_positions = len(portfolio_state.positions) > 0
    if target_mode in (EmergencyStopMode.EMERGENCY_HALT, EmergencyStopMode.LIQUIDATING):
        if not has_open_positions:
            target_mode = EmergencyStopMode.HALTED
            trigger_reasons.append(
                "All positions liquidated. Emergency stop transitioned to terminal HALTED."
            )
        else:
            target_mode = EmergencyStopMode.LIQUIDATING
    elif target_mode == EmergencyStopMode.HALTED:
        if has_open_positions:
            # Cannot be HALTED if positions remain open; liquidate them
            target_mode = EmergencyStopMode.LIQUIDATING
        else:
            target_mode = EmergencyStopMode.HALTED

    # 3. Action Generation (Cancellations and Liquidation Orders)
    orders_to_cancel: list[str] = []
    liquidation_orders: list[EmergencyLiquidationOrder] = []
    is_gate_locked: bool = target_mode != EmergencyStopMode.NORMAL
    is_liquidation_required: bool = False

    if target_mode == EmergencyStopMode.LIQUIDATING:
        is_liquidation_required = True
        # Cancel all pending entry/intake orders immediately
        orders_to_cancel.extend(pending_order_ids)

        # Generate deterministic liquidation orders based on scope
        scope_symbols = (
            set(specific_symbols)
            if liquidation_scope == LiquidationScope.SPECIFIC_SYMBOLS
            else None
        )

        for pos in portfolio_state.positions:
            # Check scope filtering
            if liquidation_scope == LiquidationScope.CORE_ONLY and pos.sleeve != CapitalSleeve.CORE:
                continue
            if scope_symbols is not None and pos.symbol not in scope_symbols:
                continue

            liq_order_id = f"LIQ-{pos.symbol}-{int(evaluation_timestamp.timestamp())}"
            reason_str = (
                f"Emergency safety liquidation ({trigger_type.value if trigger_type else 'HALT'})"
            )
            liquidation_orders.append(
                EmergencyLiquidationOrder(
                    order_id=liq_order_id,
                    symbol=pos.symbol,
                    shares=pos.shares,
                    side="SELL",
                    order_type="MARKET",
                    target_sleeve=pos.sleeve,
                    reason=reason_str,
                    created_at=evaluation_timestamp,
                )
            )

    elif target_mode == EmergencyStopMode.PAUSED:
        # PAUSE prevents new exposure while continuing normal management of positions (FR-015)
        is_liquidation_required = False
        # Cancel pending intake orders so no new exposure enters
        orders_to_cancel.extend(pending_order_ids)
        # No liquidation orders generated! Existing positions stay intact.

    elif target_mode == EmergencyStopMode.HALTED:
        is_liquidation_required = False
        orders_to_cancel.extend(pending_order_ids)

    # 4. Construct Deterministic Receipt ID & Audit Receipt
    if receipt_id is None:
        receipt_id = (
            f"EMERGENCY-RECEIPT-{target_mode.value}-{int(evaluation_timestamp.timestamp())}"
        )

    return EmergencyActionReceipt(
        receipt_id=receipt_id,
        timestamp=evaluation_timestamp,
        previous_mode=current_mode,
        current_mode=target_mode,
        trigger_type=trigger_type,
        trigger_reasons=tuple(trigger_reasons),
        orders_to_cancel=tuple(orders_to_cancel),
        liquidation_orders=tuple(liquidation_orders),
        is_gate_locked=is_gate_locked,
        is_liquidation_required=is_liquidation_required,
    )


def create_locked_gate_context(
    emergency_receipt: EmergencyActionReceipt,
    base_context: SystemFeedContext,
) -> SystemFeedContext:
    """Derive an execution gate SystemFeedContext reflecting the emergency stop state.

    If emergency stop or pause is active (is_gate_locked=True), the derived context sets
    is_emergency_stop_active=True, guaranteeing the Execution Gate strictly blocks
    all downstream order submissions (FR-012, FR-015).
    """
    return SystemFeedContext(
        is_system_operational=base_context.is_system_operational,
        is_broker_connected=base_context.is_broker_connected,
        is_emergency_stop_active=emergency_receipt.is_gate_locked
        or base_context.is_emergency_stop_active,
        evaluation_timestamp=emergency_receipt.timestamp,
        feed_timestamp=base_context.feed_timestamp,
        max_feed_staleness_seconds=base_context.max_feed_staleness_seconds,
    )


def enforce_emergency_stop_clearance(emergency_receipt: EmergencyActionReceipt) -> None:
    """Enforce that emergency stop is inactive before proceeding with non-emergency operations.

    Raises:
        EmergencyStopActiveError: If emergency stop or pause is active.
    """
    if emergency_receipt.current_mode != EmergencyStopMode.NORMAL:
        reasons_msg = "; ".join(emergency_receipt.trigger_reasons) or "Emergency mode active."
        raise EmergencyStopActiveError(
            f"Rejected: Emergency stop in mode '{emergency_receipt.current_mode.value}'. "
            f"Reasons: {reasons_msg}"
        )
