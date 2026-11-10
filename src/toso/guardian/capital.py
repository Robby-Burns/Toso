"""TOSO Deterministic Guardian Capital Separation & Reserve Isolation Evaluator.

Pure, deterministic evaluation of:
- Strict capital partitioning: Core (80%), Reserve (15%), Black Swan (5%) (FR-010, FR-011).
- Enforce Core trading cannot encroach on Reserve or Black Swan sleeves (INV-012, INV-013).
- 4-tier decision hierarchy separating Core exhaustion from encroachments (FINDING-001).
- Independence from broker cash / buying power (FINDING-005, FINDING-006).
- Organic appreciation immunity without liquidation (FINDING-004, MISSING-001).
- Fail-closed handling for UNKNOWN, missing, NaN, or non-positive inputs (INV-008).
- Pure audit assessment receipt generation (INV-001, INV-006).
"""

import math

from toso.config.models import CapitalAllocationConfig
from toso.guardian.errors import (
    BlackSwanEncroachmentError,
    CapitalSeparationError,
    CoreCapitalExhaustedError,
    ReserveEncroachmentError,
    UnknownCapitalInputError,
)
from toso.guardian.models import (
    CapitalAllocationAssessment,
    CapitalSleeve,
    CapitalSleeveStatus,
    PortfolioStateSnapshot,
    ProposedExposure,
)

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownCapitalInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownCapitalInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownCapitalInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownCapitalInputError(f"Field '{name}' cannot be negative, got {val}")


def calculate_sleeve_allocations(
    portfolio_equity: float,
    config: CapitalAllocationConfig,
) -> tuple[float, float, float]:
    """Calculate dollar allocations for (core, reserve, black_swan) sleeves.

    Returns:
        tuple of (core_capacity_dollars, reserve_isolated_dollars, black_swan_isolated_dollars)
    """
    _validate_float(portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(config.core_allocation_pct, "config.core_allocation_pct", must_be_positive=True)
    _validate_float(
        config.reserve_allocation_pct, "config.reserve_allocation_pct", must_be_positive=False
    )
    _validate_float(
        config.black_swan_allocation_pct, "config.black_swan_allocation_pct", must_be_positive=False
    )

    core_dollars = round(portfolio_equity * config.core_allocation_pct, 4)
    reserve_dollars = round(portfolio_equity * config.reserve_allocation_pct, 4)
    black_swan_dollars = round(portfolio_equity * config.black_swan_allocation_pct, 4)

    return core_dollars, reserve_dollars, black_swan_dollars


def evaluate_capital_intake(
    portfolio_state: PortfolioStateSnapshot,
    proposed_exposure: ProposedExposure,
    config: CapitalAllocationConfig,
) -> CapitalAllocationAssessment:
    """Pure deterministic evaluation of capital separation and sleeve isolation.

    Guarantees:
    - Pure calculation with zero I/O or wall-clock reads (INV-001, INV-006).
    - Echoes timestamp directly from portfolio_state.timestamp.
    - Fail-closed on missing, non-positive, or NaN inputs (INV-008).
    - Sleeve filtering: non-Core positions do not consume Core capacity (FINDING-002).
    - Rejection hierarchy: CORE_EXHAUSTED -> BLACK_SWAN -> RESERVE (FINDING-001).
    - Unencumbered broker cash does not authorize Core capacity expansion (FINDING-005).
    - Organic Core appreciation is protected against liquidation, but halts additions (FINDING-004).
    """
    # 1. Fail-closed input validation (INV-008, FINDING-003)
    _validate_float(portfolio_state.portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(
        proposed_exposure.effective_market_value,
        "proposed_exposure.effective_market_value",
        must_be_positive=True,
    )
    _validate_float(config.core_allocation_pct, "config.core_allocation_pct", must_be_positive=True)
    _validate_float(
        config.reserve_allocation_pct, "config.reserve_allocation_pct", must_be_positive=False
    )
    _validate_float(
        config.black_swan_allocation_pct, "config.black_swan_allocation_pct", must_be_positive=False
    )

    # Only CORE sleeve accepts general trading intake (MISSING-002)
    if proposed_exposure.target_sleeve != CapitalSleeve.CORE:
        raise UnknownCapitalInputError(
            f"Only '{CapitalSleeve.CORE}' sleeve accepts trading intake; got target_sleeve="
            f"'{proposed_exposure.target_sleeve}'."
        )

    equity = portfolio_state.portfolio_equity
    core_capacity, reserve_isolated, black_swan_isolated = calculate_sleeve_allocations(
        equity, config
    )

    # 2. Sum current market value of positions deployed in CORE sleeve (FINDING-002)
    current_core_deployed_dollars = 0.0
    for pos in portfolio_state.positions:
        _validate_float(pos.market_value, f"position({pos.symbol}).market_value", True)
        if pos.sleeve == CapitalSleeve.CORE:
            current_core_deployed_dollars += pos.market_value

    current_core_deployed_dollars = round(current_core_deployed_dollars, 4)
    available_core_dollars = round(core_capacity - current_core_deployed_dollars, 4)

    proposed_market_value = round(proposed_exposure.effective_market_value, 4)
    post_core_deployed_dollars = round(current_core_deployed_dollars + proposed_market_value, 4)

    # 3. Decision Hierarchy Evaluation (FINDING-001)
    rejection_reasons: list[str] = []

    # Hierarchy Tier 1: Core already exhausted / saturated (e.g. organic appreciation)
    if current_core_deployed_dollars >= core_capacity - EPSILON:
        status = CapitalSleeveStatus.BLOCKED_CORE_EXHAUSTED
        rejection_reasons.append(
            f"Core capital exhausted: currently deployed ${current_core_deployed_dollars:.2f} >= "
            f"capacity ${core_capacity:.2f} ({config.core_allocation_pct * 100:.1f}% of equity). "
            "Additional Core intake is prohibited while existing positions remain protected."
        )
    # Hierarchy Tier 2: Intake breaches Core + Reserve boundary, encroaching into Black Swan
    elif post_core_deployed_dollars > (core_capacity + reserve_isolated) + EPSILON:
        status = CapitalSleeveStatus.BLOCKED_BLACK_SWAN_ENCROACHMENT
        encroachment = round(post_core_deployed_dollars - (core_capacity + reserve_isolated), 4)
        rejection_reasons.append(
            f"Proposed trade encroaches into Black Swan capital: post-trade Core exposure "
            f"${post_core_deployed_dollars:.2f} breaches Core+Reserve boundary "
            f"${core_capacity + reserve_isolated:.2f} by ${encroachment:.2f} (INV-013)."
        )
    # Hierarchy Tier 3: Intake breaches Core capacity, encroaching into Reserve
    elif post_core_deployed_dollars > core_capacity + EPSILON:
        status = CapitalSleeveStatus.BLOCKED_RESERVE_ENCROACHMENT
        encroachment = round(post_core_deployed_dollars - core_capacity, 4)
        rejection_reasons.append(
            f"Proposed trade encroaches into Reserve capital: post-trade Core exposure "
            f"${post_core_deployed_dollars:.2f} exceeds Core capacity ${core_capacity:.2f} "
            f"by ${encroachment:.2f} (INV-012)."
        )
    # Hierarchy Tier 4: Approved
    else:
        status = CapitalSleeveStatus.APPROVED

    is_approved = len(rejection_reasons) == 0
    primary_rejection_reason = rejection_reasons[0] if rejection_reasons else None

    return CapitalAllocationAssessment(
        status=status,
        is_approved=is_approved,
        target_sleeve=proposed_exposure.target_sleeve,
        portfolio_equity=equity,
        core_allocation_pct=config.core_allocation_pct,
        core_capacity_dollars=core_capacity,
        current_core_deployed_dollars=current_core_deployed_dollars,
        available_core_dollars=available_core_dollars,
        proposed_market_value=proposed_market_value,
        post_core_deployed_dollars=post_core_deployed_dollars,
        reserve_allocation_pct=config.reserve_allocation_pct,
        reserve_isolated_dollars=reserve_isolated,
        black_swan_allocation_pct=config.black_swan_allocation_pct,
        black_swan_isolated_dollars=black_swan_isolated,
        rejection_reason=primary_rejection_reason,
        rejection_reasons=tuple(rejection_reasons),
        timestamp=portfolio_state.timestamp,
    )


def enforce_capital_intake(
    portfolio_state: PortfolioStateSnapshot,
    proposed_exposure: ProposedExposure,
    config: CapitalAllocationConfig,
) -> CapitalAllocationAssessment:
    """Enforce capital sleeve isolation, dispatching typed domain exceptions upon violation."""
    assessment = evaluate_capital_intake(
        portfolio_state=portfolio_state,
        proposed_exposure=proposed_exposure,
        config=config,
    )

    if not assessment.is_approved:
        reason = assessment.rejection_reason or "Capital intake blocked by Guardian."
        if assessment.status == CapitalSleeveStatus.BLOCKED_CORE_EXHAUSTED:
            raise CoreCapitalExhaustedError(reason)
        if assessment.status == CapitalSleeveStatus.BLOCKED_BLACK_SWAN_ENCROACHMENT:
            raise BlackSwanEncroachmentError(reason)
        if assessment.status == CapitalSleeveStatus.BLOCKED_RESERVE_ENCROACHMENT:
            raise ReserveEncroachmentError(reason)
        if assessment.status == CapitalSleeveStatus.BLOCKED_UNKNOWN_INPUT:
            raise UnknownCapitalInputError(reason)
        raise CapitalSeparationError(reason)

    return assessment
