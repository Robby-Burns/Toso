"""TOSO Invalidation Exit Evaluator.

Provides deterministic functions for:
- Evaluating market data bars against open positions for invalidation breaches (AC-002).
- Detecting adverse gap openings vs intraday breaches with mutually exclusive logic.
- Executing exit orders and updating position state to CLOSED with realized PnL.
- Rejecting discretionary and agent-originated attempts to delay or override exits (AC-003).
- Strictly blocking any attempt to average down or add exposure to declining positions.
"""

import uuid
from datetime import datetime

from toso.exit.errors import (
    AveragingDownProhibitedError,
    InvalidationExitOverrideError,
    InvalidPositionStateError,
)
from toso.exit.models import (
    CoreExitOrder,
    CorePosition,
    ExitReason,
    PositionStatus,
)
from toso.signals.models import Bar


def evaluate_position_exit(
    position: CorePosition, bar: Bar
) -> tuple[CorePosition, CoreExitOrder | None]:
    """Evaluate position against incoming candlestick bar for invalidation breaches.

    Mutually exclusive evaluation:
    1. Adverse Gap Down: bar.open <= invalidation.price (opened through stop).
    2. Intraday Breach: bar.low <= invalidation.price (traded through stop).
    3. No Breach: returns (position, None).

    Args:
        position: Active CorePosition.
        bar: Market data bar for the position's symbol.

    Returns:
        Tuple of (updated_position, emitted_exit_order_or_none).

    Raises:
        InvalidPositionStateError: If called on a position that is already CLOSED.
    """
    if position.status == PositionStatus.CLOSED:
        raise InvalidPositionStateError(
            f"Position {position.position_id} is already CLOSED. "
            "Cannot evaluate exit for a closed position."
        )

    if position.status == PositionStatus.INVALIDATED and position.exit_order is not None:
        return position, position.exit_order

    stop_price = position.invalidation.price

    # Branch 1: Adverse Gap Down (market opens past stop price)
    if bar.open <= stop_price:
        exit_order = CoreExitOrder(
            exit_order_id=f"EXIT-{position.position_id}-{uuid.uuid4().hex[:6].upper()}",
            position_id=position.position_id,
            symbol=position.symbol,
            timestamp=bar.timestamp,
            side="SELL",
            quantity=position.quantity,
            trigger_price=bar.open,
            invalidation_price=stop_price,
            exit_reason=ExitReason.GAP_INVALIDATION_TRIGGERED,
            explanation=(
                f"Adverse gap down detected: bar opened at {bar.open:.2f}, "
                f"breaching stop {stop_price:.2f}."
            ),
        )
        updated_position = CorePosition(
            position_id=position.position_id,
            symbol=position.symbol,
            entry_order=position.entry_order,
            entry_price=position.entry_price,
            quantity=position.quantity,
            invalidation=position.invalidation,
            status=PositionStatus.INVALIDATED,
            opened_at=position.opened_at,
            pyramids=position.pyramids,
            average_entry_price=position.average_entry_price,
            exit_order=exit_order,
        )
        return updated_position, exit_order

    # Branch 2: Intraday Stop Breach (traded down through stop)
    if bar.low <= stop_price:
        exit_order = CoreExitOrder(
            exit_order_id=f"EXIT-{position.position_id}-{uuid.uuid4().hex[:6].upper()}",
            position_id=position.position_id,
            symbol=position.symbol,
            timestamp=bar.timestamp,
            side="SELL",
            quantity=position.quantity,
            trigger_price=stop_price,
            invalidation_price=stop_price,
            exit_reason=ExitReason.INVALIDATION_TRIGGERED,
            explanation=(
                f"Intraday stop breach detected: bar low {bar.low:.2f} "
                f"reached stop {stop_price:.2f}."
            ),
        )
        updated_position = CorePosition(
            position_id=position.position_id,
            symbol=position.symbol,
            entry_order=position.entry_order,
            entry_price=position.entry_price,
            quantity=position.quantity,
            invalidation=position.invalidation,
            status=PositionStatus.INVALIDATED,
            opened_at=position.opened_at,
            pyramids=position.pyramids,
            average_entry_price=position.average_entry_price,
            exit_order=exit_order,
        )
        return updated_position, exit_order

    # Branch 3: No Breach
    return position, None


def execute_invalidation_exit(
    position: CorePosition,
    exit_order: CoreExitOrder,
    fill_price: float,
    fill_timestamp: datetime,
) -> CorePosition:
    """Execute invalidation exit fill, transitioning position to CLOSED with realized PnL.

    Args:
        position: Position in INVALIDATED or OPEN status.
        exit_order: The matching CoreExitOrder.
        fill_price: Actual execution fill price.
        fill_timestamp: Execution fill timestamp.

    Returns:
        New frozen CorePosition with status CLOSED and realized_pnl.

    Raises:
        InvalidPositionStateError: If position is already CLOSED or exit_order does not match.
    """
    if position.status == PositionStatus.CLOSED:
        raise InvalidPositionStateError(
            f"Position {position.position_id} is already CLOSED. Cannot execute duplicate exit."
        )

    if exit_order.position_id != position.position_id:
        raise InvalidPositionStateError(
            f"Exit order position_id '{exit_order.position_id}' does not match "
            f"position '{position.position_id}'."
        )

    cost_basis = (
        position.average_entry_price
        if position.average_entry_price is not None
        else position.entry_price
    )
    realized_pnl = round((fill_price - cost_basis) * position.quantity, 4)

    return CorePosition(
        position_id=position.position_id,
        symbol=position.symbol,
        entry_order=position.entry_order,
        entry_price=position.entry_price,
        quantity=position.quantity,
        invalidation=position.invalidation,
        status=PositionStatus.CLOSED,
        opened_at=position.opened_at,
        closed_at=fill_timestamp,
        pyramids=position.pyramids,
        average_entry_price=position.average_entry_price,
        exit_order=exit_order,
        realized_pnl=realized_pnl,
    )


def reject_agent_exit_delay(
    position: CorePosition,
    agent_id: str = "ScientistAgent",
    request_type: str = "DELAY",
) -> None:
    """Strictly reject discretionary or agent requests to delay or override exit (AC-003).

    Args:
        position: The affected CorePosition.
        agent_id: Proposing entity identifier.
        request_type: Request description (e.g. DELAY, CANCEL, THESIS_REWRITE).

    Raises:
        InvalidationExitOverrideError: Always raised to enforce safety boundary.
    """
    raise InvalidationExitOverrideError(
        f"Agent '{agent_id}' attempted to {request_type} exit for position "
        f"'{position.position_id}' ({position.symbol}). Discretionary and agent overrides "
        "of deterministic invalidation exits are strictly prohibited (FR-004, AC-003)."
    )


def verify_no_averaging_down(
    position: CorePosition,
    additional_quantity: float,
    current_price: float,
) -> None:
    """Verify that proposed addition does not constitute averaging down on a losing position.

    Enforces Philosophy §2.3 ("Never Average Down") and §2.4 ("Never Add Risk to Losers").

    Args:
        position: Active CorePosition.
        additional_quantity: Proposed incremental shares.
        current_price: Current market price.

    Raises:
        AveragingDownProhibitedError: If position is declining or invalidated.
    """
    if additional_quantity <= 0:
        raise AveragingDownProhibitedError(
            f"Additional quantity must be positive, got {additional_quantity}."
        )

    if position.status == PositionStatus.INVALIDATED:
        raise AveragingDownProhibitedError(
            f"Position {position.position_id} is INVALIDATED. "
            "Cannot add exposure to an invalidated position."
        )

    if current_price < position.entry_price:
        raise AveragingDownProhibitedError(
            f"Current price ({current_price:.2f}) is below entry ({position.entry_price:.2f}). "
            "Adding exposure to a declining position is strictly prohibited (Never Average Down)."
        )

    if current_price <= position.invalidation.price:
        raise AveragingDownProhibitedError(
            f"Current price ({current_price:.2f}) is at or below invalidation stop "
            f"({position.invalidation.price:.2f}). Adding exposure is strictly prohibited."
        )
