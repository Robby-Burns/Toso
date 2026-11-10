"""TOSO Broker Ambiguity Resolution & No-Blind-Retry Engine.

Implements FR-013, INV-010, INV-009, INV-008, INV-001, INV-006:
- Reconciles ambiguous or in-flight broker order outcomes deterministically (FR-013).
- Prohibits blind retries of ambiguous or working orders (INV-010).
- Broker state strictly governs execution and position truth (INV-009).
- Fails closed on query timeouts, unreachable feeds, or malformed data (INV-008).
- Blocks conflicting symbol-level actions while an ambiguous order remains pending (INV-010).
- Pure determinism and audit receipts without wall-clock reads (INV-001, INV-006).
"""

from collections.abc import Sequence
from datetime import datetime

from toso.broker.errors import (
    ConflictingActionBlockedError,
    OrderAlreadyExecutedError,
    OrderDiscrepancyUnresolvableError,
    UnknownBrokerOrderInputError,
    UnresolvedAmbiguityError,
)
from toso.broker.models import (
    AmbiguityResolutionAction,
    AmbiguityResolutionReceipt,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
)


def resolve_ambiguity(
    order_id: str,
    symbol: str,
    broker_order: BrokerOrderSnapshot | None,
    resolution_timestamp: datetime,
    is_query_successful: bool = True,
    receipt_id: str | None = None,
) -> AmbiguityResolutionReceipt:
    """Resolve an ambiguous order outcome deterministically from broker inquiry.

    Enforces INV-010 (No Blind Retry) and INV-009 (Broker Authority):
    - Confirmed fills are adopted; retry is strictly prohibited (INV-009).
    - Working/active orders on broker are tracked; duplicate execution blocked.
    - Confirmed dropped/missing orders are marked safe for fresh submission.
    - Terminal non-filled orders (canceled, rejected) permit retry if desired.
    - Query failures or continued ambiguity fail closed (INV-008).

    Args:
        order_id: Unique client tracking order ID.
        symbol: Ticker symbol.
        broker_order: Authoritative broker order snapshot if returned, or None if not found.
        resolution_timestamp: Clock anchor echoed into audit receipt (INV-006).
        is_query_successful: True if broker inquiry completed without timeout/network error.
        receipt_id: Optional explicit receipt ID. Generated deterministically if None.

    Returns:
        Immutable AmbiguityResolutionReceipt.

    Raises:
        UnknownBrokerOrderInputError: On malformed or missing inputs (INV-008).
        OrderDiscrepancyUnresolvableError: If broker order identity conflicts with local request.
    """
    if not order_id or not isinstance(order_id, str):
        raise UnknownBrokerOrderInputError(f"Invalid order_id: {order_id}")
    if not symbol or not isinstance(symbol, str):
        raise UnknownBrokerOrderInputError(f"Invalid symbol: {symbol}")
    if resolution_timestamp is None or not isinstance(resolution_timestamp, datetime):
        raise UnknownBrokerOrderInputError(f"Invalid resolution_timestamp: {resolution_timestamp}")

    diagnostic_reasons: list[str] = []

    # Case 1: Query failed, timed out, or network unreachable -> Hold Unresolved (INV-008, INV-010)
    if not is_query_successful:
        action = AmbiguityResolutionAction.HOLD_UNRESOLVED
        is_resolved = False
        can_retry = False
        prohibit_duplicate_execution = True
        msg = (
            f"Broker inquiry for order {order_id} ({symbol}) timed out or failed; "
            "retaining AMBIGUOUS status without retry (HOLD_UNRESOLVED, INV-010)."
        )
        diagnostic_reasons.append(msg)

    # Case 2: Query succeeded, but order was not found / never received by broker
    elif broker_order is None:
        action = AmbiguityResolutionAction.CONFIRM_DROPPED
        is_resolved = True
        can_retry = True
        prohibit_duplicate_execution = False
        msg = (
            f"Broker inquiry confirmed order {order_id} ({symbol}) does not exist on broker; "
            "safe to resubmit with fresh identity or cancel (CONFIRM_DROPPED)."
        )
        diagnostic_reasons.append(msg)

    # Case 3: Broker returned order record -> Broker state strictly governs (INV-009)
    else:
        if broker_order.order_id != order_id or broker_order.symbol != symbol:
            raise OrderDiscrepancyUnresolvableError(
                f"Broker order conflict: expected {order_id} ({symbol}), "
                f"got {broker_order.order_id} ({broker_order.symbol})."
            )

        if broker_order.status == BrokerOrderStatus.FILLED:
            action = AmbiguityResolutionAction.ADOPT_FILL
            is_resolved = True
            can_retry = False
            prohibit_duplicate_execution = True
            msg = (
                f"Broker inquiry confirmed order {order_id} ({symbol}) is FILLED "
                f"({broker_order.filled_quantity} shares @ {broker_order.average_fill_price}); "
                "fill adopted authoritatively, duplicate execution blocked (ADOPT_FILL, INV-009)."
            )
            diagnostic_reasons.append(msg)

        elif broker_order.status in (
            BrokerOrderStatus.SUBMITTED,
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.PENDING_SUBMIT,
            BrokerOrderStatus.PARTIALLY_FILLED,
        ):
            action = AmbiguityResolutionAction.TRACK_ACTIVE
            is_resolved = True
            can_retry = False
            prohibit_duplicate_execution = True
            msg = (
                f"Broker inquiry confirmed order {order_id} ({symbol}) is active "
                f"({broker_order.status}); duplicate submission blocked (TRACK_ACTIVE, INV-010)."
            )
            diagnostic_reasons.append(msg)

        elif broker_order.status in (
            BrokerOrderStatus.REJECTED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
        ):
            action = AmbiguityResolutionAction.PERMIT_RETRY
            is_resolved = True
            can_retry = True
            prohibit_duplicate_execution = False
            msg = (
                f"Broker inquiry confirmed order {order_id} ({symbol}) is in terminal "
                f"non-executed state ({broker_order.status}); retry permitted (PERMIT_RETRY)."
            )
            diagnostic_reasons.append(msg)

        else:
            # Explicit AMBIGUOUS or unexpected status from broker
            action = AmbiguityResolutionAction.HOLD_UNRESOLVED
            is_resolved = False
            can_retry = False
            prohibit_duplicate_execution = True
            msg = (
                f"Broker inquiry for order {order_id} ({symbol}) returned status "
                f"{broker_order.status}; hold unresolved (HOLD_UNRESOLVED, INV-010)."
            )
            diagnostic_reasons.append(msg)

    if receipt_id is None:
        ts_sec = int(resolution_timestamp.timestamp())
        receipt_id = f"AMBIG-RES-{order_id}-{ts_sec}"

    return AmbiguityResolutionReceipt(
        receipt_id=receipt_id,
        timestamp=resolution_timestamp,
        order_id=order_id,
        symbol=symbol,
        is_resolved=is_resolved,
        action=action,
        authoritative_order=broker_order,
        can_retry=can_retry,
        prohibit_duplicate_execution=prohibit_duplicate_execution,
        diagnostic_reasons=tuple(diagnostic_reasons),
    )


def enforce_no_blind_retry(receipt: AmbiguityResolutionReceipt) -> bool:
    """Enforce strict No-Blind-Retry gating on an ambiguous order (INV-010).

    Args:
        receipt: AmbiguityResolutionReceipt from resolve_ambiguity.

    Returns:
        True if the order is confirmed safe to retry or resubmit.

    Raises:
        UnresolvedAmbiguityError: If ambiguity is still unresolved (HOLD_UNRESOLVED).
        OrderAlreadyExecutedError: If order is active or executed on broker.
    """
    if receipt.action == AmbiguityResolutionAction.HOLD_UNRESOLVED or not receipt.is_resolved:
        raise UnresolvedAmbiguityError(
            f"Blind retry prohibited: order {receipt.order_id} ({receipt.symbol}) "
            f"has unresolved ambiguity: {receipt.diagnostic_reasons[0]}"
        )

    if receipt.prohibit_duplicate_execution or not receipt.can_retry:
        raise OrderAlreadyExecutedError(
            f"Duplicate action prohibited: order {receipt.order_id} ({receipt.symbol}) "
            f"is already active or filled: {receipt.diagnostic_reasons[0]}"
        )

    return True


def enforce_symbol_action_safety(
    symbol: str,
    ambiguity_receipts: Sequence[AmbiguityResolutionReceipt],
) -> bool:
    """Enforce symbol-level lockout while an ambiguous order remains pending (INV-010).

    Prevents submitting new or conflicting actions for a symbol when an order for that symbol
    remains in an ambiguous outcome state.

    Args:
        symbol: Ticker symbol of proposed action.
        ambiguity_receipts: Active ambiguity resolution receipts.

    Returns:
        True if safe to submit action for symbol.

    Raises:
        ConflictingActionBlockedError: If an unresolved ambiguous order exists for symbol.
    """
    for receipt in ambiguity_receipts:
        if receipt.symbol == symbol and not receipt.is_resolved:
            raise ConflictingActionBlockedError(
                f"Conflicting action blocked for {symbol}: order {receipt.order_id} "
                "remains in an unresolved ambiguous state (INV-010)."
            )

    return True
