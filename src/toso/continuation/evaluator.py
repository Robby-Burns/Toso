"""TOSO Winner Continuation & Anti-Take-Profit Rule Evaluator.

Provides pure, deterministic functions for:
- Evaluating winner continuation status and organic concentration immunity (AC-003, AC-005).
- Reconciling canonical 1R dollar risk identically with pyramiding (FINDING-001).
- Intercepting and rejecting prohibited exit types (fixed targets, trimming, rebalancing,
  and discretionary agent exits) (AC-001, AC-002, AC-004).
- Fail-closed order payload allowlisting preventing profit target registration (FINDING-003).
- Integrated candlestick bar continuation and invalidation evaluation (AC-006).
"""

from datetime import datetime
from typing import Any

from toso.config.models import RiskConfig
from toso.continuation.errors import (
    DiscretionaryExitProhibitedError,
    ForcedConcentrationExitProhibitedError,
    InvalidContinuationStateError,
    PrematureTrimmingProhibitedError,
    TakeProfitProhibitedError,
)
from toso.continuation.models import (
    ContinuationStatus,
    ExitType,
    ExitValidationReceipt,
    WinnerContinuationDecision,
)
from toso.exit.evaluator import evaluate_position_exit
from toso.exit.models import CoreExitOrder, CorePosition, PositionStatus
from toso.signals.models import Bar

# Strict allowlist of permitted fields in order creation payloads (FINDING-003)
APPROVED_ORDER_FIELDS: frozenset[str] = frozenset(
    {
        "order_id",
        "exit_order_id",
        "position_id",
        "parent_position_id",
        "symbol",
        "timestamp",
        "side",
        "quantity",
        "price",
        "entry_price",
        "invalidation",
        "invalidation_price",
        "risk_accounting",
        "status",
        "trigger_price",
        "exit_reason",
        "explanation",
        "add_index",
        "nominal_risk_dollars",
        "effective_risk_dollars",
    }
)

PROHIBITED_TARGET_PATTERNS: tuple[str, ...] = (
    "target_price",
    "take_profit",
    "profit_target",
    "tp_price",
    "target_r",
    "profit_target_r",
    "limit_target",
    "trim_price",
)


def evaluate_winner_continuation(
    position: CorePosition,
    current_price: float,
    timestamp: datetime,
    portfolio_equity: float,
    risk_config: RiskConfig,
) -> WinnerContinuationDecision:
    """Deterministically evaluate position continuation, R-profit, and concentration immunity.

    Args:
        position: Active CorePosition.
        current_price: Current market price.
        timestamp: Evaluation timestamp.
        portfolio_equity: Total current portfolio equity (dollars).
        risk_config: Validated RiskConfig instance.

    Returns:
        WinnerContinuationDecision: Immutable audit receipt.

    Raises:
        InvalidContinuationStateError: If position is CLOSED or equity <= 0.
    """
    if position.status == PositionStatus.CLOSED:
        raise InvalidContinuationStateError(
            f"Position {position.position_id} is already CLOSED. "
            "Cannot evaluate continuation for a closed position."
        )

    if portfolio_equity <= 0:
        raise InvalidContinuationStateError(
            f"Portfolio equity must be strictly positive, got {portfolio_equity}."
        )

    if current_price <= 0:
        raise InvalidContinuationStateError(
            f"Current price must be strictly positive, got {current_price}."
        )

    # 1. Authoritative blended cost basis from CorePosition (FINDING-004)
    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )

    # 2. Canonical 1R dollar risk anchored to originating entry order (FINDING-001)
    orig_entry = position.entry_order.entry_price
    orig_stop = position.entry_order.invalidation.price
    initial_risk_per_share = max(0.0, round(orig_entry - orig_stop, 4))
    initial_quantity = (
        position.quantity
        if not position.pyramids
        else (position.quantity - sum(p.quantity for p in position.pyramids))
    )
    initial_risk_dollars = round(initial_quantity * initial_risk_per_share, 4)

    # 3. Mark-to-market unrealized profit & R-multiple
    unrealized_profit_dollars = round((current_price - cost_basis) * position.quantity, 4)
    unrealized_profit_r = (
        round(unrealized_profit_dollars / initial_risk_dollars, 4)
        if initial_risk_dollars > 0
        else 0.0
    )

    # 4. Position weight & organic concentration evaluation
    position_market_value = position.quantity * current_price
    position_weight_pct = round(position_market_value / portfolio_equity, 4)
    is_concentration_breached = position_weight_pct > risk_config.max_position_concentration_pct

    # 5. Status & action determination
    stop_price = position.invalidation.price
    if current_price <= stop_price or position.status == PositionStatus.INVALIDATED:
        continuation_status = ContinuationStatus.INVALIDATION_PENDING
        is_continuation_permitted = False
        action = "EXIT_REQUIRED"
        explanation = (
            f"Price (${current_price:.2f}) reached or breached stop (${stop_price:.2f}). "
            "Deterministic exit must be executed."
        )
    elif unrealized_profit_r >= 1.0 and current_price > cost_basis:
        continuation_status = ContinuationStatus.RUNNING_WINNER
        is_continuation_permitted = True
        action = "CONTINUE_HOLD"
        conc_note = (
            f" Organic weight ({position_weight_pct:.1%}) exceeds intake cap "
            f"({risk_config.max_position_concentration_pct:.1%}), but concentration is an intake "
            "constraint only (FR-008); position is protected from forced liquidation."
            if is_concentration_breached
            else ""
        )
        explanation = (
            f"Position is a qualifying running winner (+{unrealized_profit_r:.2f}R, "
            f"${unrealized_profit_dollars:,.2f} unrealized). Hold until strategy invalidation."
            f"{conc_note}"
        )
    else:
        continuation_status = ContinuationStatus.STANDARD_HOLD
        is_continuation_permitted = True
        action = "CONTINUE_HOLD"
        explanation = (
            f"Position is in standard trend hold (+{unrealized_profit_r:.2f}R). "
            f"Stop intact at ${stop_price:.2f}."
        )

    return WinnerContinuationDecision(
        position_id=position.position_id,
        symbol=position.symbol,
        timestamp=timestamp,
        current_price=current_price,
        cost_basis=cost_basis,
        initial_risk_dollars=initial_risk_dollars,
        unrealized_profit_dollars=unrealized_profit_dollars,
        unrealized_profit_r=unrealized_profit_r,
        position_weight_pct=position_weight_pct,
        max_concentration_pct=risk_config.max_position_concentration_pct,
        is_concentration_breached=is_concentration_breached,
        continuation_status=continuation_status,
        is_continuation_permitted=is_continuation_permitted,
        action=action,
        explanation=explanation,
    )


def validate_exit_request(
    position: CorePosition,
    proposed_exit_type: ExitType,
    requester_id: str = "System",
    timestamp: datetime | None = None,
) -> ExitValidationReceipt:
    """Validate a proposed exit request against anti-take-profit and winner-continuation rules.

    Enforces:
    - Zero fixed profit targets (FR-007, Philosophy §2.2).
    - Zero premature trimming / profit locking (FR-007, Philosophy §2.2).
    - Zero forced concentration rebalancing (FR-008, Philosophy §2.6).
    - Zero discretionary or agent overrides (Philosophy §2.2, §2.8).

    Args:
        position: Active CorePosition.
        proposed_exit_type: ExitType enum value.
        requester_id: Entity initiating the exit request.
        timestamp: Optional evaluation timestamp.

    Returns:
        ExitValidationReceipt: Verification receipt if approved.

    Raises:
        TakeProfitProhibitedError: On FIXED_PROFIT_TARGET.
        PrematureTrimmingProhibitedError: On PROFIT_TRIM.
        ForcedConcentrationExitProhibitedError: On CONCENTRATION_REBALANCE.
        DiscretionaryExitProhibitedError: On DISCRETIONARY_EXIT.
    """
    ts = timestamp or datetime.now()

    if proposed_exit_type == ExitType.FIXED_PROFIT_TARGET:
        raise TakeProfitProhibitedError(
            f"Requester '{requester_id}' requested FIXED_PROFIT_TARGET exit for position "
            f"'{position.position_id}' ({position.symbol}). Core positions exit solely on "
            "strategy invalidation; fixed profit targets are strictly prohibited (FR-007, §2.2)."
        )

    if proposed_exit_type == ExitType.PROFIT_TRIM:
        raise PrematureTrimmingProhibitedError(
            f"Requester '{requester_id}' requested PROFIT_TRIM for position "
            f"'{position.position_id}' ({position.symbol}). Trimming or scaling out of valid "
            "winners to lock in profits is strictly prohibited (FR-007, §2.2)."
        )

    if proposed_exit_type == ExitType.CONCENTRATION_REBALANCE:
        raise ForcedConcentrationExitProhibitedError(
            f"Requester '{requester_id}' requested CONCENTRATION_REBALANCE exit for position "
            f"'{position.position_id}' ({position.symbol}). Concentration is an intake constraint, "
            "not a forced-exit mechanism; winners cannot be liquidated to restore allocations "
            "(FR-008, §2.6)."
        )

    if proposed_exit_type == ExitType.DISCRETIONARY_EXIT:
        raise DiscretionaryExitProhibitedError(
            f"Requester '{requester_id}' requested DISCRETIONARY_EXIT for position "
            f"'{position.position_id}' ({position.symbol}). Discretionary and agent exits on "
            "uninvalidated winning positions are strictly prohibited (FR-007, §2.8)."
        )

    approved_types = {
        ExitType.INVALIDATION_STOP,
        ExitType.GAP_INVALIDATION_STOP,
        ExitType.STRATEGY_SIGNAL_EXIT,
    }

    if proposed_exit_type in approved_types:
        return ExitValidationReceipt(
            position_id=position.position_id,
            exit_type=proposed_exit_type,
            requester_id=requester_id,
            is_permitted=True,
            timestamp=ts,
            explanation=(
                f"Approved deterministic exit type '{proposed_exit_type.value}' "
                f"requested by '{requester_id}' is validated and permitted."
            ),
        )

    raise TakeProfitProhibitedError(
        f"Exit type '{proposed_exit_type}' requested by '{requester_id}' is unrecognized "
        "and prohibited."
    )


def prevent_target_price_registration(order_payload: dict[str, Any]) -> None:
    """Fail-closed allowlist interceptor scanning order payloads for prohibited target fields.

    Enforces FINDING-003: Uses an allowlist of approved fields and scans for target/profit keys.

    Args:
        order_payload: Dictionary of order initialization parameters.

    Raises:
        TakeProfitProhibitedError: If any unapproved or profit-target-like field is detected.
    """
    for key in order_payload:
        lower_key = key.lower()

        # Check for explicit prohibited pattern
        if any(pat in lower_key for pat in PROHIBITED_TARGET_PATTERNS):
            raise TakeProfitProhibitedError(
                f"Order payload contains prohibited profit target key: '{key}'. "
                "Fixed targets and target prices are strictly prohibited on Core orders (FR-007)."
            )

        # Check against strict allowlist
        if key not in APPROVED_ORDER_FIELDS:
            raise TakeProfitProhibitedError(
                f"Order payload contains unapproved field: '{key}'. "
                f"Permitted fields are: {sorted(APPROVED_ORDER_FIELDS)}."
            )


def verify_winner_continuation_on_bar(
    position: CorePosition,
    bar: Bar,
    portfolio_equity: float,
    risk_config: RiskConfig,
) -> tuple[WinnerContinuationDecision, CoreExitOrder | None]:
    """Integrated bar evaluation: checks invalidation breach vs winner continuation.

    Args:
        position: Active CorePosition.
        bar: Candlestick Bar for the position's symbol.
        portfolio_equity: Total portfolio equity in dollars.
        risk_config: Validated RiskConfig instance.

    Returns:
        Tuple of (WinnerContinuationDecision, CoreExitOrder | None).
    """
    # 1. Evaluate market data bar against invalidation stop
    updated_pos, exit_order = evaluate_position_exit(position, bar)

    # 2. Evaluate continuation metrics
    decision = evaluate_winner_continuation(
        position=updated_pos,
        current_price=bar.close,
        timestamp=bar.timestamp,
        portfolio_equity=portfolio_equity,
        risk_config=risk_config,
    )

    return decision, exit_order
