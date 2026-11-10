"""TOSO Black Swan Defensive Capital Partitioning & Intake Evaluator.

Implements FR-010, FR-011, INV-012, INV-013, INV-008, INV-001, INV-006:
- Enforces strict capital partitioning for Black Swan defense (FR-010).
- Guarantees complete independence of Black Swan defense from Core trading (FR-011).
- Prohibits Black Swan defensive actions from breaching sleeve allocations (INV-013).
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
- Fail-closed handling for UNKNOWN, NaN, infinite, or corrupted inputs (INV-008).
"""

import math
from datetime import datetime

from toso.config.models import CapitalAllocationConfig
from toso.defense.errors import (
    BlackSwanCapitalExhaustedError,
    BlackSwanSleeveBreachError,
    UnknownDefenseInputError,
)
from toso.defense.models import (
    BlackSwanCapitalAssessment,
    BlackSwanCapitalSnapshot,
    BlackSwanIntakeStatus,
)
from toso.guardian.models import CapitalSleeve, PortfolioStateSnapshot

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownDefenseInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownDefenseInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownDefenseInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownDefenseInputError(f"Field '{name}' cannot be negative, got {val}")


def calculate_black_swan_capital(
    portfolio_state: PortfolioStateSnapshot,
    config: CapitalAllocationConfig,
    timestamp: datetime,
) -> BlackSwanCapitalSnapshot:
    """Calculate the authoritative defensive capital allocation and capacity snapshot.

    Args:
        portfolio_state: Current PortfolioStateSnapshot providing authoritative equity.
        config: Approved CapitalAllocationConfig specifying black_swan_allocation_pct.
        timestamp: Timestamp anchor echoed into the snapshot.

    Returns:
        Immutable BlackSwanCapitalSnapshot.

    Raises:
        UnknownDefenseInputError: On invalid, NaN, infinite, or non-positive inputs (INV-008).
    """
    if not isinstance(portfolio_state, PortfolioStateSnapshot):
        raise UnknownDefenseInputError(f"Invalid portfolio_state: {type(portfolio_state)}")
    if not isinstance(config, CapitalAllocationConfig):
        raise UnknownDefenseInputError(f"Invalid config: {type(config)}")
    if timestamp is None or not isinstance(timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid timestamp: {timestamp}")

    _validate_float(portfolio_state.portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(
        config.black_swan_allocation_pct, "black_swan_allocation_pct", must_be_positive=True
    )

    allocated_budget = round(portfolio_state.portfolio_equity * config.black_swan_allocation_pct, 4)

    # Sum current open positions deployed in the Black Swan sleeve
    deployed_market_value = sum(
        p.market_value for p in portfolio_state.positions if p.sleeve == CapitalSleeve.BLACK_SWAN
    )
    deployed_market_value = round(deployed_market_value, 4)

    available_capacity = max(0.0, round(allocated_budget - deployed_market_value, 4))

    return BlackSwanCapitalSnapshot(
        portfolio_equity=portfolio_state.portfolio_equity,
        allocation_pct=config.black_swan_allocation_pct,
        allocated_budget_dollars=allocated_budget,
        deployed_market_value_dollars=deployed_market_value,
        available_capacity_dollars=available_capacity,
        timestamp=timestamp,
    )


def evaluate_black_swan_capital_intake(
    portfolio_state: PortfolioStateSnapshot,
    proposed_market_value: float,
    config: CapitalAllocationConfig,
    evaluation_timestamp: datetime,
    target_sleeve: CapitalSleeve = CapitalSleeve.BLACK_SWAN,
    assessment_id: str | None = None,
) -> BlackSwanCapitalAssessment:
    """Evaluate whether a proposed defensive expenditure fits within Black Swan capital budget.

    Enforces FR-010 (Capital Separation), FR-011 (Independence), and INV-013.

    Args:
        portfolio_state: Current authoritative portfolio state.
        proposed_market_value: Dollar market value of proposed defensive trade.
        config: Approved CapitalAllocationConfig.
        evaluation_timestamp: Clock anchor echoed into the audit receipt.
        target_sleeve: Sleeve targeted by the trade (must be CapitalSleeve.BLACK_SWAN).
        assessment_id: Optional explicit assessment ID. Generated deterministically if None.

    Returns:
        Immutable BlackSwanCapitalAssessment audit receipt.

    Raises:
        UnknownDefenseInputError: On malformed, NaN, infinite, or negative inputs (INV-008).
    """
    _validate_float(proposed_market_value, "proposed_market_value", must_be_positive=False)

    snapshot = calculate_black_swan_capital(
        portfolio_state=portfolio_state,
        config=config,
        timestamp=evaluation_timestamp,
    )

    diagnostic_reasons: list[str] = []

    # Check 1: Target sleeve must be BLACK_SWAN
    if target_sleeve != CapitalSleeve.BLACK_SWAN:
        status = BlackSwanIntakeStatus.SLEEVE_BREACH
        is_approved = False
        msg = (
            f"Defensive trade targeting sleeve {target_sleeve} rejected: "
            "Black Swan defensive trades must strictly target CapitalSleeve.BLACK_SWAN (INV-013)."
        )
        diagnostic_reasons.append(msg)

    # Check 2: Available defensive capacity
    elif proposed_market_value > snapshot.available_capacity_dollars + EPSILON:
        status = BlackSwanIntakeStatus.CAPITAL_EXHAUSTED
        is_approved = False
        msg = (
            f"Proposed defensive allocation (${proposed_market_value:,.2f}) exceeds "
            f"available capacity (${snapshot.available_capacity_dollars:,.2f}) "
            f"within total defensive budget (${snapshot.allocated_budget_dollars:,.2f})."
        )
        diagnostic_reasons.append(msg)

    else:
        status = BlackSwanIntakeStatus.APPROVED
        is_approved = True
        msg = (
            f"Proposed defensive allocation (${proposed_market_value:,.2f}) approved "
            f"within available capacity (${snapshot.available_capacity_dollars:,.2f})."
        )
        diagnostic_reasons.append(msg)

    if assessment_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        assessment_id = f"BS-CAP-{ts_sec}-{int(proposed_market_value)}"

    return BlackSwanCapitalAssessment(
        assessment_id=assessment_id,
        timestamp=evaluation_timestamp,
        status=status,
        capital_snapshot=snapshot,
        proposed_market_value=proposed_market_value,
        is_approved=is_approved,
        diagnostic_reasons=tuple(diagnostic_reasons),
    )


def enforce_black_swan_intake_safety(assessment: BlackSwanCapitalAssessment) -> bool:
    """Enforce Black Swan capital intake safety gating.

    Args:
        assessment: BlackSwanCapitalAssessment from evaluate_black_swan_capital_intake.

    Returns:
        True if defensive expenditure is approved.

    Raises:
        BlackSwanSleeveBreachError: If proposed trade breaches sleeve boundaries.
        BlackSwanCapitalExhaustedError: If defensive capital capacity is exhausted.
        UnknownDefenseInputError: If evaluation failed closed.
    """
    if assessment.status == BlackSwanIntakeStatus.FAILED_CLOSED:
        raise UnknownDefenseInputError(
            f"Black Swan intake evaluation failed closed: {assessment.diagnostic_reasons}"
        )

    if assessment.status == BlackSwanIntakeStatus.SLEEVE_BREACH:
        raise BlackSwanSleeveBreachError(assessment.diagnostic_reasons[0])

    if assessment.status == BlackSwanIntakeStatus.CAPITAL_EXHAUSTED or not assessment.is_approved:
        raise BlackSwanCapitalExhaustedError(assessment.diagnostic_reasons[0])

    return True
