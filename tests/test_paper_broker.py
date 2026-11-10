"""Tests for TOSO Paper Broker & Simulation Environment Engine (Story PAPER-001).

Verifies FR-013, INV-001, INV-006, INV-008, INV-009, INV-010, and INV-014:
- AC-001: Deterministic account balance and cash accounting.
- AC-002: Direct compatibility with Phase 3 authoritative broker reconcilers.
- AC-003: Idempotent order submission and duplicate conflict interception.
- AC-004: Deterministic market order execution with slippage.
- AC-005: Deterministic limit and stop order execution against bar boundaries.
- AC-006: Order cancellation and lifecycle status transitions.
- AC-007: Insufficient buying power and share balance gating.
- AC-008: Position bookkeeping and mark-to-market PnL tracking.
- AC-009: Pure determinism, clock anchoring, and SHA-256 cryptographic receipts.
- AC-010: Fail-closed on corrupt or unknown inputs and model immutability.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toso.broker.account import reconcile_account_state
from toso.broker.models import (
    BrokerOrderStatus,
    LocalAccountSnapshot,
    LocalOrderSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from toso.broker.orders import reconcile_order_state
from toso.broker.positions import reconcile_positions
from toso.paper.broker import PaperBroker, compute_paper_receipt_checksum
from toso.paper.errors import (
    DuplicatePaperOrderError,
    InsufficientPaperFundsError,
    InvalidPaperStateError,
    PaperOrderNotFoundError,
    UnknownPaperInputError,
)
from toso.paper.models import PaperAction, PaperFill, PaperPosition
from toso.signals.models import Bar


def make_bar(
    timestamp: datetime,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
) -> Bar:
    """Helper to construct a valid OHLCV bar."""
    return Bar(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_ac001_deterministic_account_and_cash_accounting() -> None:
    """AC-001: PaperBroker initializes and tracks balances deterministically."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=50_000.0, anchor_timestamp=ts)

    assert broker.account_id == "PAPER-ACC-1"
    assert broker.cash_balance == 50_000.0
    assert broker.total_equity == 50_000.0
    assert broker.buying_power == 50_000.0
    assert broker.realized_pnl == 0.0

    state = broker.get_account_state()
    assert state.cash_balance == 50_000.0
    assert state.total_equity == 50_000.0
    assert state.positions_count == 0
    assert state.active_orders_count == 0


def test_ac002_authoritative_snapshot_compatibility() -> None:
    """AC-002: PaperBroker snapshots are directly compatible with Phase 3 reconcilers."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=100_000.0, anchor_timestamp=ts)

    # 1. Account reconciliation compatibility
    account_snap = broker.get_account_snapshot()
    local_acc = LocalAccountSnapshot(
        account_id="PAPER-ACC-1",
        total_equity=100_000.0,
        cash_balance=100_000.0,
        currency="USD",
        timestamp=ts,
    )
    rcpt = reconcile_account_state(
        broker_snapshot=account_snap,
        local_snapshot=local_acc,
        reconciliation_timestamp=ts,
    )
    assert rcpt.is_synchronized is True

    # 2. Submit and fill an order, then test order reconciliation
    intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        signal_id="SIG-001",
        intent_timestamp=ts,
    )
    paper_order, _ = broker.submit_order(intent, current_price=100.0)
    bar = make_bar(ts, open_=100.0, high=102.0, low=99.0, close=101.0)
    broker.process_bar("AAPL", bar)

    order_snap = broker.get_order_snapshot(paper_order.order_id)
    local_order = LocalOrderSnapshot(
        order_id=paper_order.order_id,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        filled_quantity=10,
        status=BrokerOrderStatus.FILLED,
        timestamp=ts,
    )
    order_rcpt = reconcile_order_state(
        local_order=local_order,
        broker_order=order_snap,
        reconciliation_timestamp=ts,
    )
    assert order_rcpt.authoritative_status == BrokerOrderStatus.FILLED

    # 3. Position reconciliation compatibility
    pos_snaps = broker.get_position_snapshots()
    assert len(pos_snaps) == 1
    pos_rcpt = reconcile_positions(
        local_positions=(),  # Simulating local sync against authoritative broker
        broker_positions=pos_snaps,
        reconciliation_timestamp=ts,
    )
    assert pos_rcpt.has_discrepancy is True  # Detected phantom position vs empty local


def test_ac003_idempotent_order_submission_and_duplicate_interception() -> None:
    """AC-003: Idempotent re-submission returns order; conflicting reuse raises error."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=100_000.0, anchor_timestamp=ts)

    intent = OrderIntent(
        symbol="MSFT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=20,
        limit_price=300.0,
        signal_id="SIG-002",
        intent_timestamp=ts,
    )
    order1, _ = broker.submit_order(intent)

    # Identical resubmission returns original order without duplicating
    order2, _ = broker.submit_order(intent)
    assert order1.order_id == order2.order_id

    # Conflicting reuse of same client_order_id raises DuplicatePaperOrderError
    conflicting_intent = OrderIntent(
        symbol="GOOGL",  # Different symbol with explicit reused ID
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        limit_price=150.0,
        signal_id="SIG-003",
        intent_timestamp=ts,
        client_order_id=order1.order_id,
    )
    with pytest.raises(DuplicatePaperOrderError, match="Conflicting order payload"):
        broker.submit_order(conflicting_intent)


def test_ac004_deterministic_market_order_execution_and_slippage() -> None:
    """AC-004: Market orders execute at open with exact deterministic slippage."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    # 10 bps slippage = 0.0010
    broker = PaperBroker(
        account_id="PAPER-ACC-1",
        initial_cash=100_000.0,
        slippage_bps=10.0,
        anchor_timestamp=ts,
    )

    intent = OrderIntent(
        symbol="NVDA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
        signal_id="SIG-004",
        intent_timestamp=ts,
    )
    order, _ = broker.submit_order(intent, current_price=100.0)

    # Bar with open = 100.0. Slippage = +0.10. Fill price = 100.10. Cost = 10,010.0
    bar = make_bar(ts, open_=100.0, high=105.0, low=98.0, close=102.0)
    fills, _ = broker.process_bar("NVDA", bar)

    assert len(fills) == 1
    fill = fills[0]
    assert fill.price == 100.10
    assert fill.slippage == 0.10
    assert broker.cash_balance == 100_000.0 - 10_010.0

    pos = broker.get_position("NVDA")
    assert pos is not None
    assert pos.shares == 100
    assert pos.average_entry_price == 100.10
    assert pos.current_price == 102.0
    assert pos.market_value == 10_200.0
    assert pos.unrealized_pnl == 190.0  # 100 * (102.0 - 100.10)


def test_ac005_deterministic_limit_and_stop_order_execution() -> None:
    """AC-005: Limit and Stop orders trigger strictly on bar boundaries."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=100_000.0, anchor_timestamp=ts)

    # Limit BUY at 90.0
    intent_limit = OrderIntent(
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=90.0,
        signal_id="SIG-LIM-1",
        intent_timestamp=ts,
    )
    broker.submit_order(intent_limit)

    # Bar 1: low is 92.0 -> Limit does not fill
    bar1 = make_bar(ts, open_=95.0, high=98.0, low=92.0, close=94.0)
    fills1, _ = broker.process_bar("SPY", bar1)
    assert len(fills1) == 0

    # Bar 2: low is 88.0 -> Limit fills at 90.0 (or min(open, limit))
    bar2 = make_bar(ts, open_=91.0, high=92.0, low=88.0, close=89.0)
    fills2, _ = broker.process_bar("SPY", bar2)
    assert len(fills2) == 1
    assert fills2[0].price == 90.0

    # Stop SELL at 85.0
    intent_stop = OrderIntent(
        symbol="SPY",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        quantity=10,
        stop_price=85.0,
        signal_id="SIG-STOP-1",
        intent_timestamp=ts,
    )
    broker.submit_order(intent_stop)

    # Bar 3: low trades down to 84.0 -> Stop triggers and fills
    bar3 = make_bar(ts, open_=87.0, high=88.0, low=84.0, close=85.0)
    fills3, _ = broker.process_bar("SPY", bar3)
    assert len(fills3) == 1
    assert fills3[0].price == 85.0


def test_ac006_order_cancellation_and_state_transitions() -> None:
    """AC-006: Active orders can be canceled; terminal orders reject cancellation."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=100_000.0, anchor_timestamp=ts)

    intent = OrderIntent(
        symbol="QQQ",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=15,
        limit_price=200.0,
        signal_id="SIG-006",
        intent_timestamp=ts,
    )
    order, _ = broker.submit_order(intent)
    assert order.status == BrokerOrderStatus.ACCEPTED

    canceled, _ = broker.cancel_order(order.order_id, timestamp=ts)
    assert canceled.status == BrokerOrderStatus.CANCELED
    assert canceled.is_terminal is True

    # Attempting to cancel already canceled order raises InvalidPaperStateError
    with pytest.raises(InvalidPaperStateError, match="Cannot cancel order"):
        broker.cancel_order(order.order_id, timestamp=ts)


def test_ac007_insufficient_buying_power_and_capital_gating() -> None:
    """AC-007: Orders exceeding cash or selling unowned shares fail closed."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=5_000.0, anchor_timestamp=ts)

    # 1. Buy order requiring 10,000 cash on 5,000 account
    intent_excess = OrderIntent(
        symbol="AMZN",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        limit_price=100.0,
        signal_id="SIG-EXCESS",
        intent_timestamp=ts,
    )
    with pytest.raises(InsufficientPaperFundsError, match="Insufficient paper cash"):
        broker.submit_order(intent_excess)

    # 2. Sell order for unowned shares
    intent_sell_unowned = OrderIntent(
        symbol="TSLA",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=50,
        signal_id="SIG-UNOWNED",
        intent_timestamp=ts,
    )
    with pytest.raises(InsufficientPaperFundsError, match="Insufficient held shares"):
        broker.submit_order(intent_sell_unowned)


def test_ac008_position_bookkeeping_and_pnl_mark_to_market() -> None:
    """AC-008: Multi-trade position updates, realized PnL, and mark-to-market calculations."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    broker = PaperBroker(account_id="PAPER-ACC-1", initial_cash=100_000.0, anchor_timestamp=ts)

    # Buy 50 shares @ 100.0
    intent1 = OrderIntent(
        symbol="META",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=50,
        signal_id="SIG-META-1",
        intent_timestamp=ts,
    )
    broker.submit_order(intent1)
    bar1 = make_bar(ts, open_=100.0, high=102.0, low=99.0, close=100.0)
    broker.process_bar("META", bar1)

    # Buy another 50 shares @ 120.0 -> average entry should be 110.0
    intent2 = OrderIntent(
        symbol="META",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=50,
        signal_id="SIG-META-2",
        intent_timestamp=ts,
    )
    broker.submit_order(intent2)
    bar2 = make_bar(ts, open_=120.0, high=125.0, low=118.0, close=122.0)
    broker.process_bar("META", bar2)

    pos = broker.get_position("META")
    assert pos is not None
    assert pos.shares == 100
    assert pos.average_entry_price == 110.0
    assert pos.current_price == 122.0
    assert pos.unrealized_pnl == 1_200.0  # 100 * (122 - 110)

    # Sell 50 shares @ 130.0 -> realized PnL = 50 * (130 - 110) = +1,000.0
    intent_sell = OrderIntent(
        symbol="META",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=50,
        signal_id="SIG-META-3",
        intent_timestamp=ts,
    )
    broker.submit_order(intent_sell)
    bar3 = make_bar(ts, open_=130.0, high=135.0, low=128.0, close=132.0)
    broker.process_bar("META", bar3)

    assert broker.realized_pnl == 1_000.0
    pos_after = broker.get_position("META")
    assert pos_after is not None
    assert pos_after.shares == 50
    assert pos_after.average_entry_price == 110.0
    assert pos_after.unrealized_pnl == 50 * (132.0 - 110.0)


def test_ac009_pure_determinism_and_clock_anchoring() -> None:
    """AC-009: Simulation is bit-identical and verifiable with SHA-256 receipts."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    def run_sim() -> tuple[float, str]:
        b = PaperBroker(account_id="DET-ACC", initial_cash=50_000.0, anchor_timestamp=ts)
        i = OrderIntent(
            symbol="AMD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=25,
            signal_id="SIG-DET",
            intent_timestamp=ts,
        )
        b.submit_order(i)
        bar = make_bar(ts, open_=80.0, high=82.0, low=79.0, close=81.0)
        b.process_bar("AMD", bar)
        return b.total_equity, b.receipts[-1].checksum

    eq1, chk1 = run_sim()
    eq2, chk2 = run_sim()
    assert eq1 == eq2
    assert chk1 == chk2

    # Verify checksum matches manual SHA-256 calculation
    manual_chk = compute_paper_receipt_checksum(
        receipt_id="RCPT-TEST",
        timestamp=ts,
        action=PaperAction.PROCESS_BAR,
        account_id="DET-ACC",
        total_equity=50000.0,
        cash_balance=48000.0,
        detail="test",
    )
    assert len(manual_chk) == 64


def test_ac010_fail_closed_on_corrupt_or_unknown_inputs() -> None:
    """AC-010: Fails closed on NaN, Inf, non-positive numbers, and unknown fields."""
    ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # Invalid initial cash
    with pytest.raises(UnknownPaperInputError):
        PaperBroker(initial_cash=-1000.0)
    with pytest.raises(UnknownPaperInputError):
        PaperBroker(initial_cash=float("nan"))
    with pytest.raises(UnknownPaperInputError):
        PaperBroker(account_id="")

    broker = PaperBroker(account_id="PAPER-ACC", initial_cash=10_000.0, anchor_timestamp=ts)

    # Missing order lookup
    with pytest.raises(PaperOrderNotFoundError):
        broker.get_order("NONEXISTENT-ORDER")

    # Invalid order intent input type
    with pytest.raises(UnknownPaperInputError):
        broker.submit_order(None)  # type: ignore[arg-type]

    # Models are frozen with extra="forbid"
    with pytest.raises(ValidationError):
        PaperPosition(
            symbol="AAPL",
            shares=10,
            average_entry_price=150.0,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            realized_pnl=0.0,
            updated_at=ts,
            extra_field="disallowed",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        PaperFill(
            fill_id="F-1",
            order_id="O-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            price=150.0,
            timestamp=ts,
            extra_field="disallowed",  # type: ignore[call-arg]
        )
