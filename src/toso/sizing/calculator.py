"""TOSO Deterministic Position Sizing Engine.

Calculates executable position sizes strictly accounting for:
- Single-trade risk budget and portfolio risk ceiling.
- Execution slippage buffer in sizing denominator.
- Intake concentration limits.
- Core capital sleeve capacity.
- Adverse overnight gap scenario stress tests.
"""

import math
from datetime import UTC, datetime

from toso.config.models import CapitalAllocationConfig, RiskConfig
from toso.entry.models import CoreEntryOrder
from toso.sizing.errors import (
    ConcentrationLimitExceededError,
    InsufficientCapitalError,
    InvalidSizingInputError,
    PortfolioRiskLimitExceededError,
)
from toso.sizing.models import (
    GapScenarioLoss,
    PositionSizingDecision,
    SizingConstraint,
)


def verify_concentration_limit(
    market_value: float,
    portfolio_equity: float,
    risk_config: RiskConfig,
) -> None:
    """Verify that an explicit position market value does not breach concentration limits.

    Fails closed with ConcentrationLimitExceededError if market_value exceeds the limit.
    """
    if portfolio_equity <= 0.0:
        raise InvalidSizingInputError(
            f"Portfolio equity must be strictly positive, got {portfolio_equity}."
        )
    if market_value < 0.0:
        raise InvalidSizingInputError(f"Market value cannot be negative, got {market_value}.")

    max_concentration_dollars = portfolio_equity * risk_config.max_position_concentration_pct
    # Use small epsilon (1e-6) to guard against minor floating point representation imprecision
    if market_value > max_concentration_dollars + 1e-6:
        concentration_pct = market_value / portfolio_equity
        max_pct = risk_config.max_position_concentration_pct
        raise ConcentrationLimitExceededError(
            f"Position market value ${market_value:.2f} ({concentration_pct:.2%}) "
            f"exceeds maximum concentration limit of {max_pct:.2%} "
            f"(${max_concentration_dollars:.2f})."
        )


def calculate_position_size(
    entry_order: CoreEntryOrder,
    portfolio_equity: float,
    risk_config: RiskConfig,
    capital_config: CapitalAllocationConfig,
    current_open_risk_dollars: float = 0.0,
    current_deployed_core_dollars: float = 0.0,
    gap_scenarios: tuple[float, ...] = (0.05, 0.10, 0.20),
) -> PositionSizingDecision:
    """Calculate deterministic position size incorporating slippage buffer and capital constraints.

    Formulas:
    - effective_risk_per_share = nominal_risk + (entry_price * slippage_buffer_pct)
    - risk_shares = floor(target_risk_dollars / effective_risk_per_share)
    - concentration_shares = floor((equity * max_concentration_pct) / entry_price)
    - capital_shares = floor(available_core_dollars / entry_price)
    - allocated_shares = min(risk_shares, concentration_shares, capital_shares)
    """
    # 1. Input validations
    if portfolio_equity <= 0.0:
        raise InvalidSizingInputError(
            f"Portfolio equity must be strictly positive, got {portfolio_equity}."
        )
    if current_open_risk_dollars < 0.0:
        raise InvalidSizingInputError(
            f"Current open risk dollars cannot be negative, got {current_open_risk_dollars}."
        )
    if current_deployed_core_dollars < 0.0:
        raise InvalidSizingInputError(
            f"Deployed core dollars cannot be negative, got {current_deployed_core_dollars}."
        )
    if entry_order.entry_price <= 0.0:
        raise InvalidSizingInputError(
            f"Entry price must be strictly positive, got {entry_order.entry_price}."
        )
    if entry_order.risk_accounting.effective_risk_per_share <= 0.0:
        raise InvalidSizingInputError(
            "Effective risk per share must be strictly positive to size positions."
        )

    # 2. Portfolio Risk Ceiling & Available Risk Budget
    max_portfolio_risk_dollars = round(portfolio_equity * risk_config.max_portfolio_risk_pct, 4)
    available_risk_dollars = round(max_portfolio_risk_dollars - current_open_risk_dollars, 4)

    if available_risk_dollars <= 0.0:
        raise PortfolioRiskLimitExceededError(
            f"Portfolio risk limit reached or exceeded: available risk is "
            f"${available_risk_dollars:.2f} (max allowed: ${max_portfolio_risk_dollars:.2f}, "
            f"current open: ${current_open_risk_dollars:.2f})."
        )

    single_trade_risk_budget = round(portfolio_equity * risk_config.max_risk_per_trade_pct, 4)
    target_risk_dollars = min(single_trade_risk_budget, available_risk_dollars)

    # 3. Available Core Capital Allocation Ceiling
    max_core_dollars = round(portfolio_equity * capital_config.core_allocation_pct, 4)
    available_core_dollars = round(max_core_dollars - current_deployed_core_dollars, 4)

    if available_core_dollars <= 0.0:
        raise InsufficientCapitalError(
            f"Core capital allocation exhausted: available core capital is "
            f"${available_core_dollars:.2f} (max core: ${max_core_dollars:.2f}, "
            f"currently deployed: ${current_deployed_core_dollars:.2f})."
        )

    # 4. Concentration Ceiling
    max_concentration_dollars = round(
        portfolio_equity * risk_config.max_position_concentration_pct, 4
    )

    # 5. Share Derivation across constraints
    effective_risk_per_share = entry_order.risk_accounting.effective_risk_per_share
    risk_shares = math.floor(target_risk_dollars / effective_risk_per_share)
    concentration_shares = math.floor(max_concentration_dollars / entry_order.entry_price)
    capital_shares = math.floor(available_core_dollars / entry_order.entry_price)

    allocated_shares = min(risk_shares, concentration_shares, capital_shares)

    # Determine binding constraint
    if allocated_shares == concentration_shares and concentration_shares < risk_shares:
        binding_constraint = SizingConstraint.CONCENTRATION_LIMIT
    elif allocated_shares == capital_shares and capital_shares < risk_shares:
        binding_constraint = SizingConstraint.CORE_CAPITAL_LIMIT
    else:
        binding_constraint = SizingConstraint.RISK_BUDGET

    # Fail closed if unable to buy at least 1 discrete share
    if allocated_shares < 1:
        raise InsufficientCapitalError(
            f"Calculated share size ({allocated_shares}) is less than 1 minimum executable share "
            f"under risk budget (${target_risk_dollars:.2f}) and effective risk "
            f"(${effective_risk_per_share:.2f}/share)."
        )

    # 6. Sizing Metrics
    market_value = round(allocated_shares * entry_order.entry_price, 4)
    nominal_risk_dollars = round(
        allocated_shares * entry_order.risk_accounting.nominal_risk_per_share, 4
    )
    effective_risk_dollars = round(allocated_shares * effective_risk_per_share, 4)
    concentration_pct = round(market_value / portfolio_equity, 6)

    # 7. Adverse Gap Stress Scenarios
    scenarios: list[GapScenarioLoss] = []
    for gap_pct in gap_scenarios:
        loss_per_share = entry_order.risk_accounting.calculate_gap_loss(
            entry_order.entry_price, gap_pct
        )
        total_scenario_loss = round(allocated_shares * loss_per_share, 4)
        pct_equity = round(total_scenario_loss / portfolio_equity, 6)
        scenarios.append(
            GapScenarioLoss(
                gap_down_pct=gap_pct,
                loss_per_share=loss_per_share,
                total_scenario_loss_dollars=total_scenario_loss,
                scenario_loss_pct_equity=pct_equity,
            )
        )

    explanation = (
        f"Sized {allocated_shares} shares of {entry_order.symbol} "
        f"at ${entry_order.entry_price:.2f}. "
        f"Bound by {binding_constraint.value}. "
        f"Effective risk: ${effective_risk_dollars:.2f} (budget: ${target_risk_dollars:.2f}). "
        f"Market value: ${market_value:.2f} ({concentration_pct:.2%} of equity)."
    )

    return PositionSizingDecision(
        symbol=entry_order.symbol,
        timestamp=datetime.now(UTC),
        portfolio_equity=portfolio_equity,
        risk_budget_dollars=target_risk_dollars,
        entry_price=entry_order.entry_price,
        invalidation_price=entry_order.invalidation.price,
        nominal_risk_per_share=entry_order.risk_accounting.nominal_risk_per_share,
        slippage_buffer_pct=entry_order.risk_accounting.slippage_buffer_pct,
        effective_risk_per_share=effective_risk_per_share,
        allocated_shares=allocated_shares,
        market_value=market_value,
        nominal_risk_dollars=nominal_risk_dollars,
        effective_risk_dollars=effective_risk_dollars,
        concentration_pct=concentration_pct,
        binding_constraint=binding_constraint,
        gap_scenarios=tuple(scenarios),
        explanation=explanation,
    )
