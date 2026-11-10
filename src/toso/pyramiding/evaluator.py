"""TOSO Winner-Only Pyramiding and Aggregate Risk Evaluator.

Provides deterministic evaluation of:
- Winner eligibility (profit >= min_profit_r, upward stepping, max adds).
- Aggregate position and portfolio risk assessment.
- Concentration gating without liquidation.
- Anti-loosening enforcement on pyramid stop ratchets.
- Application of pyramid orders to update CorePosition state.
"""

import math
import uuid
from datetime import UTC, datetime

from toso.config.models import RiskConfig, StrategyConfig
from toso.entry.errors import InvalidationLooseningError
from toso.entry.models import InvalidationDefinition
from toso.exit.models import CorePosition, PositionStatus
from toso.pyramiding.errors import (
    AggregateRiskLimitExceededError,
    InsufficientCapitalError,
    MaxPyramidAddsExceededError,
    PortfolioRiskLimitExceededError,
    PyramidConcentrationExceededError,
    PyramidNotEligibleError,
)
from toso.pyramiding.models import (
    AggregateRiskAssessment,
    PyramidEligibility,
    PyramidOrder,
)


def evaluate_pyramid_eligibility(
    position: CorePosition,
    current_price: float,
    strategy_config: StrategyConfig,
) -> PyramidEligibility:
    """Evaluate whether an active position satisfies deterministic winner criteria to pyramid.

    Enforces:
    - Position must be OPEN.
    - current_price > effective cost basis (Never Average Down).
    - current_price > last addition price (strictly upward additions).
    - Unrealized profit >= strategy_config.pyramid_min_profit_r * 1R.
    - Number of existing adds < strategy_config.max_pyramid_adds.
    - 1R is immutably anchored to original position.entry_order.
    """
    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )
    last_add_price = position.pyramids[-1].price if position.pyramids else position.entry_price
    current_adds = len(position.pyramids)

    # 1R anchored strictly to original entry order
    orig_entry = position.entry_order.entry_price
    orig_stop = position.entry_order.invalidation.price
    initial_risk_per_share = max(0.0, round(orig_entry - orig_stop, 4))
    initial_quantity = (
        position.quantity
        if not position.pyramids
        else (position.quantity - sum(p.quantity for p in position.pyramids))
    )
    initial_risk_dollars = round(initial_quantity * initial_risk_per_share, 4)

    # Mark-to-market unrealized profit against blended cost basis
    unrealized_profit_dollars = round((current_price - cost_basis) * position.quantity, 4)
    r_multiple = (
        round(unrealized_profit_dollars / initial_risk_dollars, 4)
        if initial_risk_dollars > 0
        else 0.0
    )

    rejection: str | None = None

    if position.status != PositionStatus.OPEN:
        rejection = (
            f"Position status is {position.status.value}, not OPEN. "
            "Cannot pyramid inactive positions."
        )
    elif current_price <= cost_basis:
        rejection = (
            f"Current price (${current_price:.2f}) is at or below cost basis "
            f"(${cost_basis:.2f}). Adding exposure to non-winning positions is strictly "
            "prohibited (Never Average Down)."
        )
    elif current_price <= last_add_price:
        rejection = (
            f"Current price (${current_price:.2f}) is at or below last add price "
            f"(${last_add_price:.2f}). Pyramiding requires strictly upward additions."
        )
    elif current_adds >= strategy_config.max_pyramid_adds:
        rejection = (
            f"Maximum pyramid additions ({strategy_config.max_pyramid_adds}) already "
            f"reached for position {position.position_id}."
        )
    elif r_multiple < strategy_config.pyramid_min_profit_r:
        rejection = (
            f"Unrealized profit of {r_multiple:.2f}R is below required threshold of "
            f"{strategy_config.pyramid_min_profit_r:.2f}R."
        )

    is_eligible = rejection is None

    return PyramidEligibility(
        position_id=position.position_id,
        is_eligible=is_eligible,
        current_price=current_price,
        initial_risk_dollars=initial_risk_dollars,
        unrealized_profit_dollars=unrealized_profit_dollars,
        r_multiple=r_multiple,
        current_adds_count=current_adds,
        max_adds_allowed=strategy_config.max_pyramid_adds,
        rejection_reason=rejection,
    )


def calculate_aggregate_risk(
    position: CorePosition,
    add_quantity: int,
    add_price: float,
    new_stop_price: float,
    portfolio_equity: float,
    risk_config: RiskConfig,
) -> AggregateRiskAssessment:
    """Calculate resulting aggregate position metrics and capital risk upon adding exposure."""
    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )
    total_shares = int(position.quantity) + add_quantity
    weighted_average_entry = round(
        ((position.quantity * cost_basis) + (add_quantity * add_price)) / total_shares,
        4,
    )
    resulting_market_value = round(total_shares * add_price, 4)

    # Net nominal dollar risk from weighted average entry down to new_stop_price
    nominal_aggregate_risk = max(
        0.0,
        round(total_shares * (weighted_average_entry - new_stop_price), 4),
    )

    # Effective risk incorporates execution slippage buffer across the combined position
    slippage_allowance = round(resulting_market_value * risk_config.slippage_buffer_pct, 4)
    effective_aggregate_risk = round(nominal_aggregate_risk + slippage_allowance, 4)

    aggregate_risk_pct_equity = round(effective_aggregate_risk / portfolio_equity, 6)
    resulting_concentration_pct = round(resulting_market_value / portfolio_equity, 6)

    return AggregateRiskAssessment(
        position_id=position.position_id,
        total_shares=total_shares,
        weighted_average_entry=weighted_average_entry,
        new_stop_price=new_stop_price,
        nominal_aggregate_risk_dollars=nominal_aggregate_risk,
        effective_aggregate_risk_dollars=effective_aggregate_risk,
        aggregate_risk_pct_equity=aggregate_risk_pct_equity,
        resulting_market_value=resulting_market_value,
        resulting_concentration_pct=resulting_concentration_pct,
    )


def create_pyramid_order(
    position: CorePosition,
    add_price: float,
    new_invalidation: InvalidationDefinition,
    portfolio_equity: float,
    risk_config: RiskConfig,
    strategy_config: StrategyConfig,
    current_open_risk_dollars: float = 0.0,
    target_add_quantity: int | None = None,
) -> PyramidOrder:
    """Evaluate and create an approved pyramid addition order under all risk constraints.

    Enforces:
    - Winner qualification (profit >= pyramid_min_profit_r).
    - Anti-loosening on stop ratchet (new_stop >= current_stop).
    - Aggregate position risk <= max_aggregate_position_risk_pct.
    - Portfolio risk ceiling <= max_portfolio_risk_pct.
    - Concentration gating: breaches reject addition without liquidating position.
    """
    # 1. Evaluate winner eligibility
    eligibility = evaluate_pyramid_eligibility(position, add_price, strategy_config)
    if not eligibility.is_eligible:
        if eligibility.current_adds_count >= strategy_config.max_pyramid_adds:
            raise MaxPyramidAddsExceededError(eligibility.rejection_reason)
        raise PyramidNotEligibleError(eligibility.rejection_reason)

    # 2. Enforce anti-loosening on stop ratchet (INV-005)
    if new_invalidation.price < position.invalidation.price:
        raise InvalidationLooseningError(
            f"New pyramid stop price (${new_invalidation.price:.2f}) cannot loosen current "
            f"position stop price (${position.invalidation.price:.2f})."
        )

    # 3. Determine discrete integer add share quantity
    if target_add_quantity is None:
        single_trade_risk_budget = portfolio_equity * risk_config.max_risk_per_trade_pct
        effective_add_risk_per_share = (add_price - new_invalidation.price) + (
            add_price * risk_config.slippage_buffer_pct
        )
        if effective_add_risk_per_share <= 0:
            effective_add_risk_per_share = add_price * risk_config.slippage_buffer_pct
        add_quantity = math.floor(single_trade_risk_budget / effective_add_risk_per_share)
    else:
        add_quantity = target_add_quantity

    if add_quantity < 1:
        raise InsufficientCapitalError(
            f"Calculated pyramid addition quantity ({add_quantity}) is less than 1 discrete share."
        )

    # 4. Calculate aggregate risk across the combined position
    assessment = calculate_aggregate_risk(
        position=position,
        add_quantity=add_quantity,
        add_price=add_price,
        new_stop_price=new_invalidation.price,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
    )

    # 5. Enforce concentration intake gating (AC-003)
    max_concentration_dollars = portfolio_equity * risk_config.max_position_concentration_pct
    if assessment.resulting_market_value > max_concentration_dollars + 1e-6:
        raise PyramidConcentrationExceededError(
            f"Proposed pyramid would result in market value "
            f"${assessment.resulting_market_value:.2f} "
            f"({assessment.resulting_concentration_pct:.2%}), exceeding maximum concentration "
            f"cap of {risk_config.max_position_concentration_pct:.2%} "
            f"(${max_concentration_dollars:.2f}). Adding exposure is blocked; existing winning "
            "position remains open without liquidation."
        )

    # 6. Enforce aggregate position risk ceiling (AC-005)
    max_aggregate_risk_dollars = portfolio_equity * risk_config.max_aggregate_position_risk_pct
    if assessment.effective_aggregate_risk_dollars > max_aggregate_risk_dollars + 1e-6:
        raise AggregateRiskLimitExceededError(
            f"Resulting aggregate position risk ${assessment.effective_aggregate_risk_dollars:.2f} "
            f"({assessment.aggregate_risk_pct_equity:.2%}) exceeds maximum aggregate position "
            f"risk limit of {risk_config.max_aggregate_position_risk_pct:.2%} "
            f"(${max_aggregate_risk_dollars:.2f})."
        )

    # 7. Enforce portfolio open risk ceiling
    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )
    existing_nominal_risk = max(
        0.0,
        round(position.quantity * (cost_basis - position.invalidation.price), 4),
    )
    existing_slippage = round(position.quantity * cost_basis * risk_config.slippage_buffer_pct, 4)
    existing_effective_risk = round(existing_nominal_risk + existing_slippage, 4)

    resulting_portfolio_risk = round(
        current_open_risk_dollars
        - existing_effective_risk
        + assessment.effective_aggregate_risk_dollars,
        4,
    )
    max_portfolio_risk_dollars = portfolio_equity * risk_config.max_portfolio_risk_pct
    if resulting_portfolio_risk > max_portfolio_risk_dollars + 1e-6:
        raise PortfolioRiskLimitExceededError(
            f"Resulting portfolio risk ${resulting_portfolio_risk:.2f} exceeds maximum portfolio "
            f"risk ceiling of {risk_config.max_portfolio_risk_pct:.2%} "
            f"(${max_portfolio_risk_dollars:.2f})."
        )

    add_index = len(position.pyramids) + 1
    order_id = f"PYR-{position.position_id}-{add_index}-{uuid.uuid4().hex[:6].upper()}"
    explanation = (
        f"Pyramid addition #{add_index}: {add_quantity} shares of {position.symbol} at "
        f"${add_price:.2f}. Stop ratcheted to ${new_invalidation.price:.2f}. "
        f"Aggregate risk: ${assessment.effective_aggregate_risk_dollars:.2f} "
        f"({assessment.aggregate_risk_pct_equity:.2%}). "
        f"Market value: ${assessment.resulting_market_value:.2f} "
        f"({assessment.resulting_concentration_pct:.2%})."
    )

    return PyramidOrder(
        pyramid_order_id=order_id,
        position_id=position.position_id,
        symbol=position.symbol,
        timestamp=datetime.now(UTC),
        side="BUY",
        add_index=add_index,
        price=add_price,
        quantity=add_quantity,
        new_invalidation=new_invalidation,
        explanation=explanation,
    )


def apply_pyramid_to_position(
    position: CorePosition,
    pyramid_order: PyramidOrder,
) -> CorePosition:
    """Apply an approved pyramid order to an existing position, updating quantity and stop."""
    if pyramid_order.position_id != position.position_id:
        raise ValueError(
            f"Pyramid order position_id '{pyramid_order.position_id}' does not match "
            f"position '{position.position_id}'."
        )

    if pyramid_order.new_invalidation.price < position.invalidation.price:
        raise InvalidationLooseningError(
            f"Cannot apply pyramid with loosened stop price "
            f"(${pyramid_order.new_invalidation.price:.2f}) "
            f"relative to active stop (${position.invalidation.price:.2f})."
        )

    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )
    new_quantity = position.quantity + pyramid_order.quantity
    new_average_entry = round(
        ((position.quantity * cost_basis) + (pyramid_order.quantity * pyramid_order.price))
        / new_quantity,
        4,
    )

    return CorePosition(
        position_id=position.position_id,
        symbol=position.symbol,
        entry_order=position.entry_order,
        entry_price=position.entry_price,
        quantity=new_quantity,
        invalidation=pyramid_order.new_invalidation,
        status=PositionStatus.OPEN,
        opened_at=position.opened_at,
        pyramids=position.pyramids + (pyramid_order,),
        average_entry_price=new_average_entry,
        exit_order=None,
        realized_pnl=None,
    )
