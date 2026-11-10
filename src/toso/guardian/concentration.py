"""TOSO Deterministic Guardian Concentration Intake Evaluator.

Pure, deterministic evaluation of:
- Single-stock position concentration intake limit gating (FR-008, §2.6).
- Organic concentration appreciation immunity and additional-intake prohibition.
- Machine-readable status determination without string parsing (FINDING-003).
- Fail-closed handling for UNKNOWN, missing, NaN, or non-positive inputs (INV-008).
- Pure audit assessment receipt generation (INV-001, INV-006).
"""

import math

from toso.config.models import RiskConfig
from toso.guardian.errors import (
    ConcentrationIntakeBlockedError,
    OrganicConcentrationAddProhibitedError,
    UnknownConcentrationInputError,
)
from toso.guardian.models import (
    ConcentrationAssessment,
    ConcentrationIntakeStatus,
    PortfolioStateSnapshot,
    ProposedExposure,
)

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownConcentrationInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownConcentrationInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownConcentrationInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownConcentrationInputError(f"Field '{name}' cannot be negative, got {val}")


def calculate_position_concentration(market_value: float, portfolio_equity: float) -> float:
    """Calculate single-position concentration weight as a fraction of portfolio equity."""
    _validate_float(market_value, "market_value", must_be_positive=False)
    _validate_float(portfolio_equity, "portfolio_equity", must_be_positive=True)

    return round(market_value / portfolio_equity, 6)


def evaluate_concentration_intake(
    proposed_exposure: ProposedExposure,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
) -> ConcentrationAssessment:
    """Pure deterministic evaluation of concentration intake limits.

    Guarantees:
    - Pure calculation with zero I/O or wall-clock reads (INV-001, INV-006).
    - Echoes timestamp directly from portfolio_state.timestamp.
    - Fail-closed on missing, non-positive, or NaN inputs (INV-008).
    - Correctly treats absent symbols as 0.0 existing exposure (FINDING-004).
    - Distinguishes organic appreciation immunity from intake prohibition (FR-008, §2.6).
    - Provides machine-readable ConcentrationIntakeStatus (FINDING-003).
    """
    # 1. Fail-closed input validations (INV-008, MISSING-003)
    _validate_float(portfolio_state.portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(
        proposed_exposure.effective_market_value,
        "proposed_exposure.effective_market_value",
        must_be_positive=True,
    )
    _validate_float(
        risk_config.max_position_concentration_pct,
        "risk_config.max_position_concentration_pct",
        must_be_positive=True,
    )

    if risk_config.max_position_concentration_pct > 1.0:
        raise UnknownConcentrationInputError(
            f"max_position_concentration_pct cannot exceed 1.0 (100%), got "
            f"{risk_config.max_position_concentration_pct}"
        )

    equity = portfolio_state.portfolio_equity
    max_cap = risk_config.max_position_concentration_pct

    # Validate all existing positions fail-closed (FINDING-004)
    current_position_market_value = 0.0
    for pos in portfolio_state.positions:
        _validate_float(pos.market_value, f"position({pos.symbol}).market_value", True)
        if pos.symbol == proposed_exposure.symbol:
            current_position_market_value += pos.market_value

    current_position_market_value = round(current_position_market_value, 4)
    current_concentration_pct = calculate_position_concentration(
        current_position_market_value, equity
    )

    # 2. Check if position has already organically breached the intake concentration cap
    is_currently_breached = current_concentration_pct > max_cap + EPSILON

    # 3. Calculate post-trade position market value and projected concentration
    post_position_market_value = round(
        current_position_market_value + proposed_exposure.effective_market_value, 4
    )
    post_concentration_pct = calculate_position_concentration(post_position_market_value, equity)

    rejection_reasons: list[str] = []

    # 4. Determine status and rejection reasons (FINDING-002, FINDING-003)
    if is_currently_breached:
        status = ConcentrationIntakeStatus.BLOCKED_ORGANIC_BREACH
        rejection_reasons.append(
            f"Position for {proposed_exposure.symbol} has organically breached concentration cap "
            f"({current_concentration_pct:.2%} > {max_cap:.2%}); additional intake is prohibited "
            "while existing position remains protected under FR-008."
        )
    elif post_concentration_pct > max_cap + EPSILON:
        status = ConcentrationIntakeStatus.BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED
        rejection_reasons.append(
            f"Proposed intake for {proposed_exposure.symbol} pushes position concentration to "
            f"{post_concentration_pct:.2%}, exceeding maximum concentration limit of {max_cap:.2%}."
        )
    else:
        status = ConcentrationIntakeStatus.APPROVED

    is_approved = len(rejection_reasons) == 0
    primary_rejection_reason = rejection_reasons[0] if rejection_reasons else None

    return ConcentrationAssessment(
        status=status,
        is_approved=is_approved,
        symbol=proposed_exposure.symbol,
        current_position_market_value=current_position_market_value,
        current_concentration_pct=current_concentration_pct,
        proposed_additional_market_value=proposed_exposure.effective_market_value,
        post_position_market_value=post_position_market_value,
        post_concentration_pct=post_concentration_pct,
        max_position_concentration_pct=max_cap,
        is_currently_breached=is_currently_breached,
        rejection_reason=primary_rejection_reason,
        rejection_reasons=tuple(rejection_reasons),
        timestamp=portfolio_state.timestamp,
    )


def enforce_concentration_intake(
    proposed_exposure: ProposedExposure,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
) -> ConcentrationAssessment:
    """Enforce concentration intake gating, dispatching domain exceptions via typed status."""
    assessment = evaluate_concentration_intake(
        proposed_exposure=proposed_exposure,
        portfolio_state=portfolio_state,
        risk_config=risk_config,
    )

    if not assessment.is_approved:
        reason = (
            assessment.rejection_reason
            or f"Concentration intake blocked for symbol {assessment.symbol}."
        )
        if assessment.status == ConcentrationIntakeStatus.BLOCKED_ORGANIC_BREACH:
            raise OrganicConcentrationAddProhibitedError(reason)
        if assessment.status == ConcentrationIntakeStatus.BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED:
            raise ConcentrationIntakeBlockedError(reason)
        if assessment.status == ConcentrationIntakeStatus.BLOCKED_UNKNOWN_INPUT:
            raise UnknownConcentrationInputError(reason)
        raise ConcentrationIntakeBlockedError(reason)

    return assessment
