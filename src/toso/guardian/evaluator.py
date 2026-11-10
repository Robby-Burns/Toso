"""TOSO Deterministic Guardian Portfolio Risk Evaluator.

Pure, deterministic evaluation of:
- Total portfolio open risk (heat) ceiling gating.
- Single-trade risk budget and per-symbol aggregate position risk ceilings.
- Peak-to-trough portfolio equity drawdown circuit breaker (HALT).
- Gross market exposure and leverage capacity.
- Fail-closed UNKNOWN/invalid input handling (INV-008).
- Pure audit assessment receipt generation (INV-001, INV-006).
"""

import math
from collections.abc import Sequence

from toso.config.models import CapitalAllocationConfig, RiskConfig
from toso.guardian.errors import (
    AggregatePositionRiskExceededError,
    GrossExposureLimitExceededError,
    GuardianRiskError,
    PortfolioDrawdownHaltError,
    PortfolioHeatExceededError,
    SingleTradeRiskExceededError,
    UnknownPortfolioRiskInputError,
)
from toso.guardian.models import (
    PortfolioRiskAssessment,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProposedExposure,
    RiskStatus,
)

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate a numeric float value to guarantee fail-closed handling on UNKNOWN/invalid data."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownPortfolioRiskInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownPortfolioRiskInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownPortfolioRiskInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownPortfolioRiskInputError(f"Field '{name}' cannot be negative, got {val}")


def calculate_portfolio_drawdown(portfolio_equity: float, peak_portfolio_equity: float) -> float:
    """Calculate peak-to-trough portfolio equity drawdown deterministically."""
    _validate_float(portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(peak_portfolio_equity, "peak_portfolio_equity", must_be_positive=True)

    if portfolio_equity >= peak_portfolio_equity:
        return 0.0

    drawdown = (peak_portfolio_equity - portfolio_equity) / peak_portfolio_equity
    return max(0.0, round(drawdown, 6))


def calculate_current_portfolio_heat(
    positions: Sequence[PositionRiskSnapshot],
    portfolio_equity: float,
) -> tuple[float, float]:
    """Calculate total open risk across all positions in dollars and as fraction of equity."""
    _validate_float(portfolio_equity, "portfolio_equity", must_be_positive=True)

    total_risk_dollars = 0.0
    for pos in positions:
        _validate_float(pos.open_risk_dollars, f"position({pos.symbol}).open_risk_dollars", False)
        total_risk_dollars += pos.open_risk_dollars

    total_risk_dollars = round(total_risk_dollars, 4)
    heat_pct = round(total_risk_dollars / portfolio_equity, 6)
    return total_risk_dollars, heat_pct


def evaluate_portfolio_risk(
    proposed_exposure: ProposedExposure,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig | None = None,
    max_gross_leverage: float = 1.0,
) -> PortfolioRiskAssessment:
    """Pure deterministic evaluation of proposed exposure against all portfolio risk controls.

    Guarantees:
    - Pure calculation with zero I/O or wall-clock side-effects (INV-001, INV-006).
    - Timestamp is echoed directly from portfolio_state.timestamp.
    - Fail-closed on any UNKNOWN, missing, NaN, or non-positive input (INV-008).
    - Accumulates all violated constraints into `rejection_reasons` (FINDING-002).
    - Preserves deterministic evaluation precedence order.
    """
    # 1. Fail-closed input validations
    _validate_float(portfolio_state.portfolio_equity, "portfolio_equity", must_be_positive=True)
    _validate_float(
        portfolio_state.peak_portfolio_equity, "peak_portfolio_equity", must_be_positive=True
    )
    _validate_float(portfolio_state.cash_balance, "cash_balance", must_be_positive=False)
    _validate_float(
        proposed_exposure.effective_risk_dollars, "effective_risk_dollars", must_be_positive=True
    )
    _validate_float(
        proposed_exposure.effective_market_value, "effective_market_value", must_be_positive=True
    )
    _validate_float(proposed_exposure.entry_price, "entry_price", must_be_positive=True)
    _validate_float(proposed_exposure.stop_loss_price, "stop_loss_price", must_be_positive=True)
    _validate_float(max_gross_leverage, "max_gross_leverage", must_be_positive=True)

    equity = portfolio_state.portfolio_equity
    peak_equity = portfolio_state.peak_portfolio_equity

    # 2. Portfolio Drawdown Check
    current_drawdown_pct = calculate_portfolio_drawdown(equity, peak_equity)

    # 3. Portfolio Heat & Position Metrics
    current_heat_dollars, current_heat_pct = calculate_current_portfolio_heat(
        portfolio_state.positions, equity
    )
    post_heat_dollars = round(current_heat_dollars + proposed_exposure.effective_risk_dollars, 4)
    post_heat_pct = round(post_heat_dollars / equity, 6)

    # 4. Gross Market Exposure
    current_gross_exposure_dollars = 0.0
    for pos in portfolio_state.positions:
        _validate_float(pos.market_value, f"position({pos.symbol}).market_value", True)
        current_gross_exposure_dollars += pos.market_value

    current_gross_exposure_dollars = round(current_gross_exposure_dollars, 4)
    post_gross_exposure_dollars = round(
        current_gross_exposure_dollars + proposed_exposure.effective_market_value, 4
    )
    gross_exposure_pct = round(post_gross_exposure_dollars / equity, 6)

    # Maximum allowable gross exposure dollars
    allowed_gross_pct = max_gross_leverage
    if capital_config is not None:
        allowed_gross_pct = min(max_gross_leverage, capital_config.core_allocation_pct)
    max_gross_dollars = round(equity * allowed_gross_pct, 4)

    # 5. Single Trade Risk
    proposed_single_trade_risk_pct = round(proposed_exposure.effective_risk_dollars / equity, 6)

    # 6. Aggregate Symbol Risk
    existing_symbol_risk_dollars = sum(
        p.open_risk_dollars
        for p in portfolio_state.positions
        if p.symbol == proposed_exposure.symbol
    )
    aggregate_symbol_risk_dollars = round(
        existing_symbol_risk_dollars + proposed_exposure.effective_risk_dollars, 4
    )
    proposed_symbol_risk_pct = round(aggregate_symbol_risk_dollars / equity, 6)

    # 7. Constraint Evaluations in Canonical Precedence Order
    rejection_reasons: list[str] = []

    # Precedence 1: Emergency Peak-to-Trough Drawdown Circuit Breaker (reaches or breaches >=)
    if current_drawdown_pct >= risk_config.emergency_drawdown_stop_pct - EPSILON:
        rejection_reasons.append(
            f"Portfolio drawdown {current_drawdown_pct:.2%} reaches or breaches "
            f"emergency stop threshold {risk_config.emergency_drawdown_stop_pct:.2%}."
        )

    # Precedence 2: Single-Trade Risk Ceiling (strictly exceeds >)
    if proposed_single_trade_risk_pct > risk_config.max_risk_per_trade_pct + EPSILON:
        rejection_reasons.append(
            f"Proposed single-trade risk {proposed_single_trade_risk_pct:.2%} "
            f"exceeds maximum allowed {risk_config.max_risk_per_trade_pct:.2%}."
        )

    # Precedence 3: Aggregate Symbol Risk Ceiling (strictly exceeds >)
    if proposed_symbol_risk_pct > risk_config.max_aggregate_position_risk_pct + EPSILON:
        rejection_reasons.append(
            f"Aggregate position risk for {proposed_exposure.symbol} "
            f"{proposed_symbol_risk_pct:.2%} exceeds maximum allowed "
            f"{risk_config.max_aggregate_position_risk_pct:.2%}."
        )

    # Precedence 4: Total Portfolio Heat Ceiling (strictly exceeds >)
    if post_heat_pct > risk_config.max_portfolio_risk_pct + EPSILON:
        rejection_reasons.append(
            f"Projected portfolio heat {post_heat_pct:.2%} exceeds "
            f"maximum allowed {risk_config.max_portfolio_risk_pct:.2%}."
        )

    # Precedence 5: Gross Market Exposure / Capacity Limit (strictly exceeds >)
    if post_gross_exposure_dollars > max_gross_dollars + EPSILON:
        rejection_reasons.append(
            f"Projected gross exposure ${post_gross_exposure_dollars:.2f} "
            f"({gross_exposure_pct:.2%}) exceeds gross capacity "
            f"${max_gross_dollars:.2f} ({allowed_gross_pct:.2%})."
        )

    # Determine Risk Status
    if current_drawdown_pct >= risk_config.emergency_drawdown_stop_pct - EPSILON:
        status = RiskStatus.HALTED
    elif len(rejection_reasons) > 0:
        status = RiskStatus.BREACH_BLOCKED
    elif post_heat_pct > 0.8 * risk_config.max_portfolio_risk_pct:
        status = RiskStatus.ELEVATED
    else:
        status = RiskStatus.NORMAL

    is_approved = len(rejection_reasons) == 0
    primary_rejection_reason = rejection_reasons[0] if rejection_reasons else None

    return PortfolioRiskAssessment(
        status=status,
        is_approved=is_approved,
        current_heat_pct=current_heat_pct,
        post_heat_pct=post_heat_pct,
        max_portfolio_risk_pct=risk_config.max_portfolio_risk_pct,
        current_drawdown_pct=current_drawdown_pct,
        emergency_drawdown_stop_pct=risk_config.emergency_drawdown_stop_pct,
        current_gross_exposure_dollars=current_gross_exposure_dollars,
        post_gross_exposure_dollars=post_gross_exposure_dollars,
        gross_exposure_pct=gross_exposure_pct,
        proposed_symbol_risk_pct=proposed_symbol_risk_pct,
        max_aggregate_position_risk_pct=risk_config.max_aggregate_position_risk_pct,
        proposed_single_trade_risk_pct=proposed_single_trade_risk_pct,
        max_risk_per_trade_pct=risk_config.max_risk_per_trade_pct,
        rejection_reason=primary_rejection_reason,
        rejection_reasons=tuple(rejection_reasons),
        timestamp=portfolio_state.timestamp,
    )


def enforce_portfolio_risk_intake(
    proposed_exposure: ProposedExposure,
    portfolio_state: PortfolioStateSnapshot,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig | None = None,
    max_gross_leverage: float = 1.0,
) -> PortfolioRiskAssessment:
    """Enforce portfolio risk controls, raising domain-specific exceptions on rejection."""
    assessment = evaluate_portfolio_risk(
        proposed_exposure=proposed_exposure,
        portfolio_state=portfolio_state,
        risk_config=risk_config,
        capital_config=capital_config,
        max_gross_leverage=max_gross_leverage,
    )

    if not assessment.is_approved:
        reason = assessment.rejection_reason or "Proposed exposure rejected by portfolio risk gate."
        if assessment.status == RiskStatus.HALTED or "drawdown" in reason.lower():
            raise PortfolioDrawdownHaltError(reason)
        if "single-trade" in reason.lower():
            raise SingleTradeRiskExceededError(reason)
        if "aggregate position risk" in reason.lower():
            raise AggregatePositionRiskExceededError(reason)
        if "portfolio heat" in reason.lower():
            raise PortfolioHeatExceededError(reason)
        if "gross exposure" in reason.lower():
            raise GrossExposureLimitExceededError(reason)
        raise GuardianRiskError(reason)

    return assessment
