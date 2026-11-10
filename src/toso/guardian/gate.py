"""TOSO Deterministic Execution Gate (Sole Authority).

Implements FR-012, INV-007, INV-008, INV-001, INV-006:
- Sole execution authority: every executable order passes through this Gate before submission.
- Three mandatory verification domains:
  1. System & Feed Check: operational status, broker connectivity, freshness, emergency stop.
  2. Intake & Risk Check: portfolio risk, concentration, capital sleeve quarantine, winner check.
  3. Execution Safety Check: order identity, protective stop, in-flight conflicts, share limits.
- Fail-closed handling for UNKNOWN, missing, stale, or malformed inputs (INV-008).
- Pure deterministic audit receipt generation with zero wall-clock reads (INV-001, INV-006).
"""

import math
from collections.abc import Callable
from typing import Any

from toso.config.models import CapitalAllocationConfig, RiskConfig
from toso.guardian.capital import calculate_sleeve_allocations, evaluate_capital_intake
from toso.guardian.concentration import evaluate_concentration_intake
from toso.guardian.errors import (
    ExecutionGateError,
    ExecutionSafetyCheckFailedError,
    IntakeRiskCheckFailedError,
    SystemFeedCheckFailedError,
    UnknownGateInputError,
)
from toso.guardian.evaluator import evaluate_portfolio_risk
from toso.guardian.models import (
    CapitalAllocationAssessment,
    CapitalSleeve,
    CapitalSleeveStatus,
    ConcentrationAssessment,
    ExecutionSafetyContext,
    GateCheckResult,
    GateDecisionStatus,
    GateDomain,
    GateExecutionReceipt,
    PortfolioRiskAssessment,
    PortfolioStateSnapshot,
    ProposedExposure,
    SystemFeedContext,
)


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric inputs for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownGateInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownGateInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownGateInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownGateInputError(f"Field '{name}' cannot be negative, got {val}")


def evaluate_execution_gate(
    order_id: str,
    symbol: str,
    shares: int,
    entry_price: float,
    stop_loss_price: float,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    system_feed_context: SystemFeedContext,
    safety_context: ExecutionSafetyContext | None = None,
    proposed_exposure: ProposedExposure | None = None,
) -> GateExecutionReceipt:
    """Pure deterministic evaluation of the three Execution Gate verification domains.

    Guarantees:
    - Pure calculation with zero I/O or wall-clock reads (INV-001, INV-006).
    - Timestamp echoed from system_feed_context.evaluation_timestamp.
    - Fail-closed on missing, stale, non-positive, or NaN inputs (INV-008).
    - Verification of all 3 required domains (FR-012).
    """
    if safety_context is None:
        safety_context = ExecutionSafetyContext()

    # 0. Fail-closed input validations (INV-008)
    if not order_id or not isinstance(order_id, str) or not order_id.strip():
        raise UnknownGateInputError("order_id must be a non-empty string.")
    if not symbol or not isinstance(symbol, str) or not symbol.strip():
        raise UnknownGateInputError("symbol must be a non-empty string.")
    if not isinstance(shares, int) or shares <= 0:
        raise UnknownGateInputError(f"shares must be a strictly positive integer, got {shares}.")

    _validate_float(entry_price, "entry_price", must_be_positive=True)
    _validate_float(stop_loss_price, "stop_loss_price", must_be_positive=True)
    _validate_float(
        system_feed_context.max_feed_staleness_seconds,
        "max_feed_staleness_seconds",
        must_be_positive=True,
    )

    eval_ts = system_feed_context.evaluation_timestamp
    feed_ts = system_feed_context.feed_timestamp

    check_results: list[GateCheckResult] = []
    rejection_reasons: list[str] = []

    # =========================================================================
    # Domain 1: System & Feed Check (FR-012 §1)
    # =========================================================================
    # 1.1 System operational
    sys_ok = system_feed_context.is_system_operational is True
    check_results.append(
        GateCheckResult(
            domain=GateDomain.SYSTEM_FEED,
            check_name="SYSTEM_OPERATIONAL",
            passed=sys_ok,
            detail=None if sys_ok else "System is not in an operational state.",
        )
    )
    if not sys_ok:
        rejection_reasons.append("System is not operational.")

    # 1.2 Broker connected
    broker_ok = system_feed_context.is_broker_connected is True
    check_results.append(
        GateCheckResult(
            domain=GateDomain.SYSTEM_FEED,
            check_name="BROKER_CONNECTED",
            passed=broker_ok,
            detail=None if broker_ok else "Broker connection is disconnected or offline.",
        )
    )
    if not broker_ok:
        rejection_reasons.append("Broker is disconnected.")

    # 1.3 Emergency stop inactive
    em_stop_ok = system_feed_context.is_emergency_stop_active is False
    check_results.append(
        GateCheckResult(
            domain=GateDomain.SYSTEM_FEED,
            check_name="EMERGENCY_STOP_INACTIVE",
            passed=em_stop_ok,
            detail=None if em_stop_ok else "Emergency stop / circuit breaker is active.",
        )
    )
    if not em_stop_ok:
        rejection_reasons.append("Emergency stop is active.")

    # 1.4 Market data feed freshness
    staleness_seconds = (eval_ts - feed_ts).total_seconds()
    if staleness_seconds < 0.0:
        feed_fresh = False
        feed_detail = f"Feed timestamp is in future relative to evaluation ({feed_ts} > {eval_ts})."
    elif staleness_seconds > system_feed_context.max_feed_staleness_seconds:
        feed_fresh = False
        feed_detail = (
            f"Market feed is stale: age {staleness_seconds:.1f}s exceeds max "
            f"{system_feed_context.max_feed_staleness_seconds:.1f}s."
        )
    else:
        feed_fresh = True
        feed_detail = None

    check_results.append(
        GateCheckResult(
            domain=GateDomain.SYSTEM_FEED,
            check_name="FEED_FRESHNESS",
            passed=feed_fresh,
            detail=feed_detail,
        )
    )
    if not feed_fresh:
        rejection_reasons.append(feed_detail or "Market feed is stale.")

    # =========================================================================
    # Domain 2: Intake & Risk Check (FR-012 §2, GUARD-001, GUARD-002, GUARD-003)
    # =========================================================================
    portfolio_risk_receipt: PortfolioRiskAssessment | None = None
    concentration_receipt: ConcentrationAssessment | None = None
    capital_receipt: CapitalAllocationAssessment | None = None

    # Construct default ProposedExposure if not provided
    if proposed_exposure is None:
        effective_risk = round(shares * max(0.0, entry_price - stop_loss_price), 4)
        effective_market_val = round(shares * entry_price, 4)
        proposed_exposure = ProposedExposure(
            symbol=symbol,
            additional_shares=shares,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            effective_risk_dollars=max(0.01, effective_risk),
            effective_market_value=max(0.01, effective_market_val),
        )

    # 2.1 Portfolio Risk Check (GUARD-001)
    if proposed_exposure.target_sleeve == CapitalSleeve.BLACK_SWAN:
        cur_drawdown = 0.0
        if portfolio_state.peak_portfolio_equity > 0:
            cur_drawdown = max(
                0.0,
                (portfolio_state.peak_portfolio_equity - portfolio_state.portfolio_equity)
                / portfolio_state.peak_portfolio_equity,
            )
        p_risk_ok = cur_drawdown < risk_config.emergency_drawdown_stop_pct - 1e-6
        p_risk_detail = None
        if not p_risk_ok:
            p_risk_detail = (
                f"Emergency drawdown halt: current drawdown {cur_drawdown:.2%} "
                f"reaches or breaches {risk_config.emergency_drawdown_stop_pct:.2%}."
            )
        check_results.append(
            GateCheckResult(
                domain=GateDomain.INTAKE_RISK,
                check_name="PORTFOLIO_RISK_LIMITS",
                passed=p_risk_ok,
                detail=p_risk_detail,
            )
        )
        if not p_risk_ok and p_risk_detail:
            rejection_reasons.append(p_risk_detail)
    else:
        portfolio_risk_receipt = evaluate_portfolio_risk(
            portfolio_state=portfolio_state,
            proposed_exposure=proposed_exposure,
            risk_config=risk_config,
            capital_config=capital_config,
        )
        p_risk_ok = portfolio_risk_receipt.is_approved
        check_results.append(
            GateCheckResult(
                domain=GateDomain.INTAKE_RISK,
                check_name="PORTFOLIO_RISK_LIMITS",
                passed=p_risk_ok,
                detail=portfolio_risk_receipt.rejection_reason,
            )
        )
        if not p_risk_ok and portfolio_risk_receipt.rejection_reason:
            rejection_reasons.append(portfolio_risk_receipt.rejection_reason)

    # 2.2 Concentration Intake Gating (GUARD-002)
    concentration_receipt = evaluate_concentration_intake(
        proposed_exposure=proposed_exposure,
        portfolio_state=portfolio_state,
        risk_config=risk_config,
    )
    conc_ok = concentration_receipt.is_approved
    check_results.append(
        GateCheckResult(
            domain=GateDomain.INTAKE_RISK,
            check_name="CONCENTRATION_LIMITS",
            passed=conc_ok,
            detail=concentration_receipt.rejection_reason,
        )
    )
    if not conc_ok and concentration_receipt.rejection_reason:
        rejection_reasons.append(concentration_receipt.rejection_reason)

    # 2.3 Capital Sleeve Separation (GUARD-003, INV-004, INV-013)
    if proposed_exposure.target_sleeve == CapitalSleeve.BLACK_SWAN:
        from toso.defense.capital import evaluate_black_swan_capital_intake

        bs_assessment = evaluate_black_swan_capital_intake(
            portfolio_state=portfolio_state,
            proposed_market_value=proposed_exposure.effective_market_value,
            config=capital_config,
            evaluation_timestamp=eval_ts,
            target_sleeve=CapitalSleeve.BLACK_SWAN,
        )
        cap_ok = bs_assessment.is_approved
        detail_msg = (
            bs_assessment.diagnostic_reasons[0]
            if (not cap_ok and bs_assessment.diagnostic_reasons)
            else None
        )
        check_results.append(
            GateCheckResult(
                domain=GateDomain.INTAKE_RISK,
                check_name="CAPITAL_SLEEVE_ISOLATION",
                passed=cap_ok,
                detail=detail_msg,
            )
        )
        if not cap_ok and detail_msg:
            rejection_reasons.append(detail_msg)

        core_cap, res_iso, bs_iso = calculate_sleeve_allocations(
            portfolio_state.portfolio_equity, capital_config
        )
        core_deployed = sum(
            p.market_value for p in portfolio_state.positions if p.sleeve == CapitalSleeve.CORE
        )
        capital_receipt = CapitalAllocationAssessment(
            status=CapitalSleeveStatus.APPROVED
            if cap_ok
            else CapitalSleeveStatus.BLOCKED_BLACK_SWAN_ENCROACHMENT,
            is_approved=cap_ok,
            target_sleeve=CapitalSleeve.BLACK_SWAN,
            portfolio_equity=portfolio_state.portfolio_equity,
            core_allocation_pct=capital_config.core_allocation_pct,
            core_capacity_dollars=core_cap,
            current_core_deployed_dollars=core_deployed,
            available_core_dollars=round(core_cap - core_deployed, 4),
            proposed_market_value=proposed_exposure.effective_market_value,
            post_core_deployed_dollars=core_deployed,
            reserve_allocation_pct=capital_config.reserve_allocation_pct,
            reserve_isolated_dollars=res_iso,
            black_swan_allocation_pct=capital_config.black_swan_allocation_pct,
            black_swan_isolated_dollars=bs_iso,
            rejection_reason=detail_msg,
            timestamp=eval_ts,
        )
    else:
        capital_receipt = evaluate_capital_intake(
            portfolio_state=portfolio_state,
            proposed_exposure=proposed_exposure,
            config=capital_config,
        )
        cap_ok = capital_receipt.is_approved
        check_results.append(
            GateCheckResult(
                domain=GateDomain.INTAKE_RISK,
                check_name="CAPITAL_SLEEVE_ISOLATION",
                passed=cap_ok,
                detail=capital_receipt.rejection_reason,
            )
        )
        if not cap_ok and capital_receipt.rejection_reason:
            rejection_reasons.append(capital_receipt.rejection_reason)

    # =========================================================================
    # Domain 3: Execution Safety Check (FR-012 §3)
    # =========================================================================
    # 3.1 Protective Invalidation Stop Check
    stop_ok = stop_loss_price < entry_price
    check_results.append(
        GateCheckResult(
            domain=GateDomain.EXECUTION_SAFETY,
            check_name="PROTECTIVE_STOP_REQUIRED",
            passed=stop_ok,
            detail=(
                None
                if stop_ok
                else (
                    f"Protective stop price {stop_loss_price} must be strictly "
                    f"below entry {entry_price}."
                )
            ),
        )
    )
    if not stop_ok:
        rejection_reasons.append(
            f"Protective stop ({stop_loss_price}) is not below entry price ({entry_price})."
        )

    # 3.2 In-Flight Order Conflict Check
    conflict_ok = symbol not in safety_context.active_in_flight_orders
    check_results.append(
        GateCheckResult(
            domain=GateDomain.EXECUTION_SAFETY,
            check_name="NO_IN_FLIGHT_CONFLICT",
            passed=conflict_ok,
            detail=(
                None
                if conflict_ok
                else f"Conflicting order for symbol '{symbol}' is already in-flight at broker."
            ),
        )
    )
    if not conflict_ok:
        rejection_reasons.append(f"Order for symbol '{symbol}' is already in-flight.")

    # 3.3 Share Quantity Bounds Check
    min_s = safety_context.min_order_shares
    max_s = safety_context.max_order_shares
    shares_ok = min_s <= shares <= max_s
    check_results.append(
        GateCheckResult(
            domain=GateDomain.EXECUTION_SAFETY,
            check_name="SHARE_QUANTITY_BOUNDS",
            passed=shares_ok,
            detail=None
            if shares_ok
            else f"Shares {shares} outside allowed range [{min_s}, {max_s}].",
        )
    )
    if not shares_ok:
        rejection_reasons.append(
            f"Share quantity {shares} outside allowable bounds [{min_s}, {max_s}]."
        )

    # Final Decision Synthesis
    is_approved = len(rejection_reasons) == 0
    if is_approved:
        status = GateDecisionStatus.APPROVED
    elif not feed_fresh or not broker_ok:
        status = GateDecisionStatus.HELD
    else:
        status = GateDecisionStatus.REJECTED

    decision_id = f"GATE-{order_id}-{int(eval_ts.timestamp())}"

    return GateExecutionReceipt(
        gate_decision_id=decision_id,
        order_id=order_id,
        symbol=symbol,
        status=status,
        is_approved=is_approved,
        portfolio_risk_assessment=portfolio_risk_receipt,
        concentration_assessment=concentration_receipt,
        capital_allocation_assessment=capital_receipt,
        check_results=tuple(check_results),
        rejection_reason=rejection_reasons[0] if rejection_reasons else None,
        rejection_reasons=tuple(rejection_reasons),
        timestamp=eval_ts,
    )


def enforce_execution_gate(
    order_id: str,
    symbol: str,
    shares: int,
    entry_price: float,
    stop_loss_price: float,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    system_feed_context: SystemFeedContext,
    safety_context: ExecutionSafetyContext | None = None,
    proposed_exposure: ProposedExposure | None = None,
) -> GateExecutionReceipt:
    """Evaluate and enforce Execution Gate, raising specific domain exceptions on block."""
    receipt = evaluate_execution_gate(
        order_id=order_id,
        symbol=symbol,
        shares=shares,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        portfolio_state=portfolio_state,
        risk_config=risk_config,
        capital_config=capital_config,
        system_feed_context=system_feed_context,
        safety_context=safety_context,
        proposed_exposure=proposed_exposure,
    )

    if not receipt.is_approved:
        reason = receipt.rejection_reason or "Order blocked by Execution Gate."
        # Determine failing domain
        failed_domains = {c.domain for c in receipt.check_results if not c.passed}
        if GateDomain.SYSTEM_FEED in failed_domains:
            raise SystemFeedCheckFailedError(reason)
        if GateDomain.INTAKE_RISK in failed_domains:
            raise IntakeRiskCheckFailedError(reason)
        if GateDomain.EXECUTION_SAFETY in failed_domains:
            raise ExecutionSafetyCheckFailedError(reason)
        raise ExecutionGateError(reason)

    return receipt


def submit_through_gate(
    order: Any,
    dispatch_fn: Callable[[Any], Any],
    order_id: str,
    symbol: str,
    shares: int,
    entry_price: float,
    stop_loss_price: float,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    system_feed_context: SystemFeedContext,
    safety_context: ExecutionSafetyContext | None = None,
    proposed_exposure: ProposedExposure | None = None,
    raise_on_rejection: bool = False,
) -> tuple[GateExecutionReceipt, Any | None]:
    """Sole Execution Authority submission wrapper (INV-007, AC-003).

    Guarantees:
    - No order can reach dispatch_fn unless evaluate_execution_gate approves.
    - If rejected, dispatch_fn is strictly NEVER called.
    - If raise_on_rejection is True, raises appropriate ExecutionGateError.
    """
    receipt = evaluate_execution_gate(
        order_id=order_id,
        symbol=symbol,
        shares=shares,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        portfolio_state=portfolio_state,
        risk_config=risk_config,
        capital_config=capital_config,
        system_feed_context=system_feed_context,
        safety_context=safety_context,
        proposed_exposure=proposed_exposure,
    )

    if not receipt.is_approved:
        if raise_on_rejection:
            reason = receipt.rejection_reason or "Order blocked by Execution Gate."
            failed_domains = {c.domain for c in receipt.check_results if not c.passed}
            if GateDomain.SYSTEM_FEED in failed_domains:
                raise SystemFeedCheckFailedError(reason)
            if GateDomain.INTAKE_RISK in failed_domains:
                raise IntakeRiskCheckFailedError(reason)
            if GateDomain.EXECUTION_SAFETY in failed_domains:
                raise ExecutionSafetyCheckFailedError(reason)
            raise ExecutionGateError(reason)
        return receipt, None

    # Gate Approved: execute sole authority dispatch
    dispatch_result = dispatch_fn(order)
    return receipt, dispatch_result
