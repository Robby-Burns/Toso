"""TOSO Black Swan Defensive Exits & De-escalation Engine.

Implements Spec Section 12.1, FR-010, FR-011, INV-013, INV-008, INV-001, INV-006:
- Enforces mandatory holding-period liquidation to eliminate volatility decay drag (RISK-005).
- Evaluates systemic crisis normalization signals to de-escalate defensive exposure.
- Realizes convexity gains through deterministic profit-taking trims.
- Calculates returned capital to accurately replenish Black Swan capacity without Core leakage.
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
- Fail-closed handling for UNKNOWN, NaN, infinite, or corrupted inputs (INV-008).
"""

import math
from datetime import datetime

from toso.defense.errors import (
    DefensiveExitError,
    InvalidDefensiveExitActionError,
    UnknownDefenseInputError,
)
from toso.defense.models import (
    DefensiveDeescalationPlan,
    DefensiveExitAction,
    DefensiveExitReason,
    DefensiveExitReceipt,
    DefensiveInstrumentSpec,
    DefensivePosition,
)


def _validate_numeric(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownDefenseInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownDefenseInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownDefenseInputError(f"Field '{name}' must be strictly positive, got {val}")


def evaluate_defensive_position_exit(
    position: DefensivePosition,
    spec: DefensiveInstrumentSpec,
    evaluation_timestamp: datetime,
    normalization_signal: bool = False,
    profit_target_pct: float | None = None,
    trim_ratio: float = 0.50,
    receipt_id: str | None = None,
) -> DefensiveExitReceipt:
    """Pure deterministic evaluator for defensive position exits and de-escalation.

    Priority of exit checks:
    1. Mandatory holding period expiry (max_holding_period_days reached) -> FULL_LIQUIDATION
    2. Crisis normalization signal active -> FULL_LIQUIDATION
    3. Convexity profit target reached -> PARTIAL_TRIM
    4. Otherwise -> HOLD

    Args:
        position: Active DefensivePosition to evaluate.
        spec: Approved DefensiveInstrumentSpec for the position asset.
        evaluation_timestamp: Current timestamp anchor echoed into receipt.
        normalization_signal: True if crisis metrics have normalized.
        profit_target_pct: Optional return threshold triggering a convexity trim.
        trim_ratio: Proportion of position to trim on profit target (default 0.50).
        receipt_id: Optional unique receipt ID.

    Returns:
        Immutable DefensiveExitReceipt.

    Raises:
        UnknownDefenseInputError: On corrupt, NaN, or non-chronological inputs.
    """
    if not isinstance(position, DefensivePosition):
        raise UnknownDefenseInputError(f"Invalid position: {type(position)}")
    if not isinstance(spec, DefensiveInstrumentSpec):
        raise UnknownDefenseInputError(f"Invalid spec: {type(spec)}")
    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid evaluation_timestamp: {evaluation_timestamp}")

    if evaluation_timestamp < position.entry_timestamp:
        raise UnknownDefenseInputError(
            f"Evaluation time ({evaluation_timestamp}) cannot precede entry "
            f"({position.entry_timestamp})."
        )

    _validate_numeric(position.current_price, "current_price", must_be_positive=True)
    _validate_numeric(trim_ratio, "trim_ratio", must_be_positive=True)
    if trim_ratio > 1.0:
        raise InvalidDefensiveExitActionError(f"trim_ratio cannot exceed 1.0, got {trim_ratio}")

    if profit_target_pct is not None:
        _validate_numeric(profit_target_pct, "profit_target_pct", must_be_positive=True)

    holding_days = (evaluation_timestamp - position.entry_timestamp).days

    if receipt_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        receipt_id = f"DEF-EXIT-{position.position_id}-{ts_sec}"

    # 1. Holding period expiry check (prevents decay drag per RISK-005)
    if spec.max_holding_period_days is not None and holding_days >= spec.max_holding_period_days:
        shares_to_exit = position.shares
        estimated_proceeds = round(shares_to_exit * position.current_price, 4)
        return DefensiveExitReceipt(
            receipt_id=receipt_id,
            position_id=position.position_id,
            symbol=position.symbol,
            action=DefensiveExitAction.FULL_LIQUIDATION,
            reason=DefensiveExitReason.HOLDING_PERIOD_EXPIRED,
            shares_to_exit=shares_to_exit,
            remaining_shares=0,
            exit_price=position.current_price,
            estimated_proceeds_dollars=estimated_proceeds,
            holding_days=holding_days,
            evaluation_timestamp=evaluation_timestamp,
            rationale=(
                f"Holding period ({holding_days}d) reached max ({spec.max_holding_period_days}d) "
                f"for {position.symbol}; fully liquidating to prevent decay drag."
            ),
        )

    # 2. Normalization signal check
    if normalization_signal:
        shares_to_exit = position.shares
        estimated_proceeds = round(shares_to_exit * position.current_price, 4)
        return DefensiveExitReceipt(
            receipt_id=receipt_id,
            position_id=position.position_id,
            symbol=position.symbol,
            action=DefensiveExitAction.FULL_LIQUIDATION,
            reason=DefensiveExitReason.NORMALIZATION_SIGNAL,
            shares_to_exit=shares_to_exit,
            remaining_shares=0,
            exit_price=position.current_price,
            estimated_proceeds_dollars=estimated_proceeds,
            holding_days=holding_days,
            evaluation_timestamp=evaluation_timestamp,
            rationale=(
                f"Market crisis normalization detected; liquidating {position.symbol} "
                "defensive position."
            ),
        )

    # 3. Profit target / convexity trim check
    if profit_target_pct is not None:
        gain = (position.current_price - position.entry_price) / position.entry_price
        if gain >= profit_target_pct:
            shares_to_exit = max(1, int(round(position.shares * trim_ratio)))
            shares_to_exit = min(shares_to_exit, position.shares)
            remaining_shares = position.shares - shares_to_exit
            action = (
                DefensiveExitAction.FULL_LIQUIDATION
                if remaining_shares == 0
                else DefensiveExitAction.PARTIAL_TRIM
            )
            estimated_proceeds = round(shares_to_exit * position.current_price, 4)
            return DefensiveExitReceipt(
                receipt_id=receipt_id,
                position_id=position.position_id,
                symbol=position.symbol,
                action=action,
                reason=DefensiveExitReason.PROFIT_TARGET_MET,
                shares_to_exit=shares_to_exit,
                remaining_shares=remaining_shares,
                exit_price=position.current_price,
                estimated_proceeds_dollars=estimated_proceeds,
                holding_days=holding_days,
                evaluation_timestamp=evaluation_timestamp,
                rationale=(
                    f"Gain of {gain:.1%} met profit target {profit_target_pct:.1%}; "
                    f"trimming {shares_to_exit} shares to realize convexity."
                ),
            )

    # 4. Default: Hold position
    return DefensiveExitReceipt(
        receipt_id=receipt_id,
        position_id=position.position_id,
        symbol=position.symbol,
        action=DefensiveExitAction.HOLD,
        reason=DefensiveExitReason.NONE,
        shares_to_exit=0,
        remaining_shares=position.shares,
        exit_price=position.current_price,
        estimated_proceeds_dollars=0.0,
        holding_days=holding_days,
        evaluation_timestamp=evaluation_timestamp,
        rationale=(
            f"Position {position.position_id} ({position.symbol}) is within limits "
            f"({holding_days}d held); continue holding."
        ),
    )


def calculate_deescalation_plan(
    positions: list[DefensivePosition] | tuple[DefensivePosition, ...],
    specs_map: dict[str, DefensiveInstrumentSpec],
    evaluation_timestamp: datetime,
    normalization_signal: bool = False,
    profit_target_pct: float | None = None,
    plan_id: str | None = None,
) -> DefensiveDeescalationPlan:
    """Calculate comprehensive de-escalation plan across all defensive positions.

    Args:
        positions: Collection of active defensive positions.
        specs_map: Map of symbol -> DefensiveInstrumentSpec.
        evaluation_timestamp: Timestamp anchor echoed into plan.
        normalization_signal: Global normalization indicator.
        profit_target_pct: Optional profit target threshold.
        plan_id: Optional unique plan identifier.

    Returns:
        Immutable DefensiveDeescalationPlan.

    Raises:
        UnknownDefenseInputError: On missing specs, corrupt collections, or invalid inputs.
    """
    if not isinstance(positions, (list, tuple)):
        raise UnknownDefenseInputError(f"positions must be a list or tuple: {type(positions)}")
    if not isinstance(specs_map, dict):
        raise UnknownDefenseInputError(f"specs_map must be a dict: {type(specs_map)}")
    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid evaluation_timestamp: {evaluation_timestamp}")

    receipts: list[DefensiveExitReceipt] = []
    total_proceeds = 0.0

    for pos in positions:
        spec = specs_map.get(pos.symbol.upper())
        if spec is None:
            raise UnknownDefenseInputError(
                f"Missing DefensiveInstrumentSpec for position symbol '{pos.symbol}'."
            )
        receipt = evaluate_defensive_position_exit(
            position=pos,
            spec=spec,
            evaluation_timestamp=evaluation_timestamp,
            normalization_signal=normalization_signal,
            profit_target_pct=profit_target_pct,
        )
        receipts.append(receipt)
        total_proceeds += receipt.estimated_proceeds_dollars

    if plan_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        plan_id = f"DEF-DEESC-PLAN-{ts_sec}"

    return DefensiveDeescalationPlan(
        plan_id=plan_id,
        exit_receipts=tuple(receipts),
        total_proceeds_replenishment_dollars=round(total_proceeds, 4),
        evaluation_timestamp=evaluation_timestamp,
    )


def enforce_defensive_exit(receipt: DefensiveExitReceipt) -> DefensiveExitReceipt:
    """Fail-closed gating helper enforcing that an exit action is required.

    Args:
        receipt: DefensiveExitReceipt from evaluate_defensive_position_exit.

    Returns:
        DefensiveExitReceipt if an exit/trim is required.

    Raises:
        DefensiveExitError: If the position evaluated to HOLD.
    """
    if receipt.action == DefensiveExitAction.HOLD:
        raise DefensiveExitError(
            f"Position {receipt.position_id} evaluated to HOLD: {receipt.rationale}"
        )
    return receipt
