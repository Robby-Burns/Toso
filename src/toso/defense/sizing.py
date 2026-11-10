"""TOSO Black Swan Defensive Sizing & Trigger Engine.

Implements FR-010, FR-011, INV-013, INV-008, INV-001, INV-006, and Spec Section 12.1:
- Evaluates deterministic systemic risk trigger signals (volatility spikes, benchmark breakdowns).
- Calculates deterministic defensive position sizes strictly bounded by:
  1. Total available Black Swan defensive capacity (FR-010, INV-013).
  2. Instrument-specific sub-allocation ceilings (Spec Section 12.1).
  3. Gap/slippage buffers applied to share acquisition price.
- Preserves absolute independence from Core position sizing (FR-011).
- Fail-closed handling for UNKNOWN, NaN, infinite, or corrupted inputs (INV-008).
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
"""

import math
from datetime import datetime

from toso.defense.errors import (
    DefensiveSizingExhaustedError,
    DefensiveSubAllocationCapExceededError,
    DefensiveTriggerConditionNotMetError,
    UnknownDefenseInputError,
)
from toso.defense.models import (
    BlackSwanCapitalSnapshot,
    DefensiveInstrumentSpec,
    DefensiveSizingReceipt,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
)


def _validate_numeric(val: float, name: str, must_be_positive: bool = False) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownDefenseInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownDefenseInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownDefenseInputError(f"Field '{name}' must be strictly positive, got {val}")


def evaluate_defensive_trigger(
    trigger_type: DefensiveTriggerType,
    observed_value: float,
    threshold_value: float,
    evaluation_timestamp: datetime,
    severity_level: int = 1,
    signal_id: str | None = None,
) -> DefensiveTriggerSignal:
    """Pure deterministic evaluator for defensive crisis trigger conditions.

    Args:
        trigger_type: Category of crisis trigger.
        observed_value: Current observed indicator metric (e.g. VIX level, return).
        threshold_value: Target threshold required to activate defense.
        evaluation_timestamp: Echoed timestamp anchor (INV-001).
        severity_level: Scale of event from 1 (mild) to 5 (extreme crisis).
        signal_id: Optional unique signal ID.

    Returns:
        Immutable DefensiveTriggerSignal receipt.

    Raises:
        UnknownDefenseInputError: If values are NaN, Inf, or corrupted.
    """
    if not isinstance(trigger_type, DefensiveTriggerType):
        raise UnknownDefenseInputError(f"Invalid trigger_type: {trigger_type}")
    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid timestamp: {evaluation_timestamp}")
    if not isinstance(severity_level, int) or severity_level < 1 or severity_level > 5:
        raise UnknownDefenseInputError(
            f"severity_level must be int between 1 and 5: {severity_level}"
        )

    _validate_numeric(observed_value, "observed_value")
    _validate_numeric(threshold_value, "threshold_value")

    is_triggered = False
    rationale = ""

    if trigger_type == DefensiveTriggerType.VOLATILITY_SPIKE:
        is_triggered = observed_value >= threshold_value
        rationale = (
            f"Observed volatility {observed_value:.2f} >= threshold {threshold_value:.2f}"
            if is_triggered
            else f"Observed volatility {observed_value:.2f} < threshold {threshold_value:.2f}"
        )
    elif trigger_type == DefensiveTriggerType.BENCHMARK_BREAKDOWN:
        is_triggered = observed_value <= threshold_value
        rationale = (
            f"Observed benchmark return {observed_value:.4f} <= threshold {threshold_value:.4f}"
            if is_triggered
            else f"Observed benchmark return {observed_value:.4f} > threshold {threshold_value:.4f}"
        )
    elif trigger_type == DefensiveTriggerType.PORTFOLIO_DRAWDOWN_WARNING:
        is_triggered = observed_value >= threshold_value
        rationale = (
            f"Observed drawdown {observed_value:.4f} >= threshold {threshold_value:.4f}"
            if is_triggered
            else f"Observed drawdown {observed_value:.4f} < threshold {threshold_value:.4f}"
        )
    elif trigger_type == DefensiveTriggerType.MANUAL_OVERRIDE:
        is_triggered = bool(observed_value)
        rationale = f"Manual override flag set to {is_triggered}"

    if signal_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        signal_id = f"DEF-TRIG-{trigger_type.value}-{ts_sec}"

    return DefensiveTriggerSignal(
        signal_id=signal_id,
        trigger_type=trigger_type,
        is_triggered=is_triggered,
        observed_value=observed_value,
        threshold_value=threshold_value,
        severity_level=severity_level,
        timestamp=evaluation_timestamp,
        rationale=rationale,
    )


def calculate_defensive_position_size(
    spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    share_price: float,
    slippage_buffer_pct: float,
    trigger_signal: DefensiveTriggerSignal,
    evaluation_timestamp: datetime,
    receipt_id: str | None = None,
) -> DefensiveSizingReceipt:
    """Calculate deterministic defensive position sizing bounded by capital limits.

    Strictly honors:
    1. Total available Black Swan defensive capacity.
    2. Sub-allocation ceiling for instrument type.
    3. Slippage buffer on share acquisition price.

    Args:
        spec: Approved DefensiveInstrumentSpec.
        capital_snapshot: Current authoritative BlackSwanCapitalSnapshot.
        share_price: Current market share price for the asset.
        slippage_buffer_pct: Slippage buffer percentage (e.g. 0.005 for 0.5%).
        trigger_signal: DefensiveTriggerSignal verifying active trigger condition.
        evaluation_timestamp: Echoed timestamp anchor (INV-001).
        receipt_id: Optional unique receipt ID.

    Returns:
        Immutable DefensiveSizingReceipt.

    Raises:
        UnknownDefenseInputError: On NaN, Inf, non-positive price, or corrupt data.
    """
    if not isinstance(spec, DefensiveInstrumentSpec):
        raise UnknownDefenseInputError(f"Invalid spec type: {type(spec)}")
    if not isinstance(capital_snapshot, BlackSwanCapitalSnapshot):
        raise UnknownDefenseInputError(f"Invalid capital_snapshot type: {type(capital_snapshot)}")
    if not isinstance(trigger_signal, DefensiveTriggerSignal):
        raise UnknownDefenseInputError(f"Invalid trigger_signal type: {type(trigger_signal)}")
    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid evaluation_timestamp: {evaluation_timestamp}")

    _validate_numeric(share_price, "share_price", must_be_positive=True)
    _validate_numeric(slippage_buffer_pct, "slippage_buffer_pct", must_be_positive=False)
    if slippage_buffer_pct < 0.0:
        raise UnknownDefenseInputError(
            f"slippage_buffer_pct cannot be negative: {slippage_buffer_pct}"
        )

    if receipt_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        receipt_id = f"DEF-SIZE-{spec.symbol.upper()}-{ts_sec}"

    # Slippage adjustment
    effective_share_price = round(share_price * (1.0 + slippage_buffer_pct), 4)

    # Calculate instrument sub-allocation dollar cap within the sleeve budget
    sub_allocation_cap_dollars = round(
        capital_snapshot.allocated_budget_dollars * spec.max_allocation_of_sleeve_pct, 4
    )

    # Inactive trigger -> zero shares
    if not trigger_signal.is_triggered:
        return DefensiveSizingReceipt(
            receipt_id=receipt_id,
            symbol=spec.symbol.upper(),
            shares=0,
            share_price=share_price,
            slippage_buffer_pct=slippage_buffer_pct,
            effective_share_price=effective_share_price,
            allocated_budget_dollars=capital_snapshot.allocated_budget_dollars,
            available_capacity_dollars=capital_snapshot.available_capacity_dollars,
            sub_allocation_cap_dollars=sub_allocation_cap_dollars,
            actual_cost_dollars=0.0,
            remaining_capacity_dollars=capital_snapshot.available_capacity_dollars,
            timestamp=evaluation_timestamp,
            rationale="Defensive trigger condition is not active; 0 shares allocated.",
        )

    # Maximum spendable dollars is constrained by both capacity and instrument cap
    spendable_dollars = min(capital_snapshot.available_capacity_dollars, sub_allocation_cap_dollars)

    if spendable_dollars < effective_share_price:
        shares = 0
        actual_cost_dollars = 0.0
        remaining_capacity = capital_snapshot.available_capacity_dollars
        rationale = (
            f"Spendable budget (${spendable_dollars:,.2f}) insufficient for single share "
            f"at effective price (${effective_share_price:,.2f})."
        )
    else:
        shares = int(spendable_dollars // effective_share_price)
        actual_cost_dollars = round(shares * effective_share_price, 4)
        remaining_capacity = round(
            capital_snapshot.available_capacity_dollars - actual_cost_dollars, 4
        )
        rationale = (
            f"Allocated {shares} shares of {spec.symbol.upper()} totaling "
            f"${actual_cost_dollars:,.2f} within spendable limit ${spendable_dollars:,.2f}."
        )

    return DefensiveSizingReceipt(
        receipt_id=receipt_id,
        symbol=spec.symbol.upper(),
        shares=shares,
        share_price=share_price,
        slippage_buffer_pct=slippage_buffer_pct,
        effective_share_price=effective_share_price,
        allocated_budget_dollars=capital_snapshot.allocated_budget_dollars,
        available_capacity_dollars=capital_snapshot.available_capacity_dollars,
        sub_allocation_cap_dollars=sub_allocation_cap_dollars,
        actual_cost_dollars=actual_cost_dollars,
        remaining_capacity_dollars=remaining_capacity,
        timestamp=evaluation_timestamp,
        rationale=rationale,
    )


def enforce_defensive_position_sizing(
    spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    share_price: float,
    slippage_buffer_pct: float,
    trigger_signal: DefensiveTriggerSignal,
    evaluation_timestamp: datetime,
) -> DefensiveSizingReceipt:
    """Enforce defensive position sizing with fail-closed exception gating.

    Args:
        spec: Approved DefensiveInstrumentSpec.
        capital_snapshot: Current authoritative BlackSwanCapitalSnapshot.
        share_price: Reference share price.
        slippage_buffer_pct: Slippage buffer allowance.
        trigger_signal: DefensiveTriggerSignal.
        evaluation_timestamp: Timestamp anchor echoed into receipt.

    Returns:
        DefensiveSizingReceipt with shares > 0.

    Raises:
        DefensiveTriggerConditionNotMetError: If trigger is not active.
        DefensiveSizingExhaustedError: If available defensive capacity is exhausted.
        DefensiveSubAllocationCapExceededError: If instrument cap prevents allocation.
        UnknownDefenseInputError: On corrupt, NaN, or invalid inputs.
    """
    if not trigger_signal.is_triggered:
        raise DefensiveTriggerConditionNotMetError(
            f"Cannot execute defensive trade: trigger {trigger_signal.trigger_type.value} "
            f"is inactive ({trigger_signal.rationale})."
        )

    receipt = calculate_defensive_position_size(
        spec=spec,
        capital_snapshot=capital_snapshot,
        share_price=share_price,
        slippage_buffer_pct=slippage_buffer_pct,
        trigger_signal=trigger_signal,
        evaluation_timestamp=evaluation_timestamp,
    )

    if receipt.shares > 0:
        return receipt

    # Sizing produced 0 shares: determine failure cause
    if receipt.available_capacity_dollars < receipt.effective_share_price:
        raise DefensiveSizingExhaustedError(
            f"Available defensive capacity (${receipt.available_capacity_dollars:,.2f}) "
            f"is exhausted and cannot purchase 1 share (${receipt.effective_share_price:,.2f})."
        )

    if receipt.sub_allocation_cap_dollars < receipt.effective_share_price:
        raise DefensiveSubAllocationCapExceededError(
            f"Instrument sub-allocation cap (${receipt.sub_allocation_cap_dollars:,.2f}) "
            f"is smaller than effective share price (${receipt.effective_share_price:,.2f})."
        )

    raise DefensiveSizingExhaustedError(receipt.rationale)
