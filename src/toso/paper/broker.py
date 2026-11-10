"""TOSO Paper Broker & Simulation Environment Engine.

Implements Phase 6 Paper Validation requirements:
FR-013, INV-001, INV-006, INV-008, INV-009, INV-010, INV-014.
- Deterministic in-memory simulated broker engine (INV-001).
- Rigorous cash, equity, buying power, and position accounting.
- Idempotent order submission and duplicate interception (INV-009, INV-010).
- Deterministic market, limit, and stop order fill simulation against market bars.
- Order cancellation and strict lifecycle status transitions.
- Authoritative snapshot generation directly compatible with Phase 3 reconcilers.
- Strict execution isolation preventing live network or order leakage (INV-014).
- Pure clock anchoring and SHA-256 cryptographic audit receipts (INV-001, INV-006).
- Fail-closed handling on corrupt, NaN, infinite, or invalid inputs (INV-008).
"""

import hashlib
import math
from datetime import datetime

from toso.broker.idempotency import generate_idempotent_order_id
from toso.broker.models import (
    BrokerAccountSnapshot,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from toso.defense.errors import (
    CoreDefensiveUniverseCollisionError,
    DisallowedDefensiveInstrumentError,
)
from toso.defense.models import DefensiveUniverseConfig
from toso.guardian.models import CapitalSleeve
from toso.paper.errors import (
    DuplicatePaperOrderError,
    InsufficientPaperFundsError,
    InvalidPaperOrderError,
    InvalidPaperStateError,
    PaperOrderNotFoundError,
    UnknownPaperInputError,
)
from toso.paper.models import (
    PaperAccountState,
    PaperAction,
    PaperBrokerReceipt,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from toso.signals.models import Bar

TERMINAL_STATUSES = frozenset(
    {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.EXPIRED,
    }
)


def _validate_finite_float(val: float, name: str, min_val: float | None = None) -> None:
    """Validate float inputs for finiteness, NaN, and minimum bounds (INV-008)."""
    if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
        raise UnknownPaperInputError(f"Field '{name}' must be a finite float, got {val}")
    if min_val is not None and val < min_val:
        raise UnknownPaperInputError(f"Field '{name}' must be >= {min_val}, got {val}")


def compute_paper_receipt_checksum(
    receipt_id: str,
    timestamp: datetime,
    action: PaperAction | str,
    account_id: str,
    total_equity: float,
    cash_balance: float,
    detail: str,
) -> str:
    """Compute deterministic SHA-256 checksum for a paper broker audit receipt (INV-001)."""
    payload = (
        f"{receipt_id}:{timestamp.isoformat()}:{action}:{account_id}:"
        f"{total_equity:.4f}:{cash_balance:.4f}:{detail}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PaperBroker:
    """Deterministic in-memory simulated broker for Paper Validation (Phase 6).

    Guarantees strict execution isolation (INV-014), pure determinism (INV-001),
    and exact alignment with authoritative broker interfaces (INV-009).
    """

    def __init__(
        self,
        account_id: str = "PAPER-ACCOUNT-001",
        initial_cash: float = 100_000.0,
        slippage_bps: float = 0.0,
        anchor_timestamp: datetime | None = None,
        defensive_universe: DefensiveUniverseConfig | None = None,
    ) -> None:
        """Initialize paper broker simulation environment."""
        if not isinstance(account_id, str) or not account_id.strip():
            raise UnknownPaperInputError("account_id must be a non-empty string.")
        _validate_finite_float(initial_cash, "initial_cash", min_val=1.0)
        _validate_finite_float(slippage_bps, "slippage_bps", min_val=0.0)

        self._account_id = account_id.strip()
        self._initial_cash = float(initial_cash)
        self._cash_balance = float(initial_cash)
        self._slippage_bps = float(slippage_bps)
        self._realized_pnl = 0.0
        self._defensive_universe = defensive_universe

        self._orders: dict[str, PaperOrder] = {}
        self._positions: dict[str, PaperPosition] = {}
        self._receipts: list[PaperBrokerReceipt] = []
        self._fill_counter = 0
        self._receipt_counter = 0

        ts = anchor_timestamp or datetime(2026, 1, 1, 0, 0, 0)
        self._last_timestamp = ts

        # Emit initial account receipt
        self._create_receipt(
            action=PaperAction.INITIALIZE,
            timestamp=ts,
            detail=(
                f"Initialized paper account {self._account_id} with cash {self._cash_balance:.2f}"
            ),
        )

    @property
    def account_id(self) -> str:
        """Return the paper account ID."""
        return self._account_id

    @property
    def cash_balance(self) -> float:
        """Return current cash balance."""
        return self._cash_balance

    @property
    def realized_pnl(self) -> float:
        """Return cumulative realized PnL."""
        return self._realized_pnl

    @property
    def total_equity(self) -> float:
        """Return total portfolio equity (cash + open positions market value)."""
        open_pos_val = sum(pos.market_value for pos in self._positions.values() if pos.shares > 0)
        return self._cash_balance + open_pos_val

    @property
    def buying_power(self) -> float:
        """Return current buying power (cash balance for cash account)."""
        return max(0.0, self._cash_balance)

    @property
    def receipts(self) -> tuple[PaperBrokerReceipt, ...]:
        """Return immutable history of audit receipts."""
        return tuple(self._receipts)

    def _next_receipt_id(self, action: PaperAction) -> str:
        self._receipt_counter += 1
        return f"RCPT-PAPER-{action}-{self._receipt_counter:06d}"

    def _next_fill_id(self) -> str:
        self._fill_counter += 1
        return f"FILL-PAPER-{self._fill_counter:06d}"

    def _create_receipt(
        self,
        action: PaperAction,
        timestamp: datetime,
        detail: str,
    ) -> PaperBrokerReceipt:
        """Generate, seal, and store an immutable audit receipt (INV-001, INV-006)."""
        receipt_id = self._next_receipt_id(action)
        eq = self.total_equity
        cash = self._cash_balance
        checksum = compute_paper_receipt_checksum(
            receipt_id=receipt_id,
            timestamp=timestamp,
            action=action,
            account_id=self._account_id,
            total_equity=eq,
            cash_balance=cash,
            detail=detail,
        )
        rcpt = PaperBrokerReceipt(
            receipt_id=receipt_id,
            timestamp=timestamp,
            action=action,
            account_id=self._account_id,
            total_equity=round(eq, 4),
            cash_balance=round(cash, 4),
            checksum=checksum,
            detail=detail,
        )
        self._receipts.append(rcpt)
        self._last_timestamp = timestamp
        return rcpt

    def submit_order(
        self,
        intent: OrderIntent,
        current_price: float | None = None,
    ) -> tuple[PaperOrder, PaperBrokerReceipt]:
        """Submit an order intent to the paper broker (FR-013, INV-009, INV-010).

        Derives deterministic client order ID, checks idempotency, validates
        cash/position requirements, and transitions order into the simulation queue.
        """
        if not isinstance(intent, OrderIntent):
            raise UnknownPaperInputError(f"intent must be an OrderIntent, got {type(intent)}")
        if not isinstance(intent.timestamp, datetime) if hasattr(intent, "timestamp") else False:
            raise UnknownPaperInputError("intent timestamp is invalid.")

        ts = intent.intent_timestamp
        if not isinstance(ts, datetime):
            raise UnknownPaperInputError("intent_timestamp must be a datetime instance.")

        # Determine client order ID
        client_order_id = intent.client_order_id or generate_idempotent_order_id(intent)

        # Idempotency check (INV-009, INV-010)
        if client_order_id in self._orders:
            existing = self._orders[client_order_id]
            # Check if payload matches
            if (
                existing.symbol != intent.symbol
                or existing.side != intent.side
                or existing.order_type != intent.order_type
                or existing.quantity != intent.quantity
            ):
                raise DuplicatePaperOrderError(
                    f"Conflicting order payload reusing order ID '{client_order_id}'."
                )
            # Idempotent re-submission returns existing order and receipt
            rcpt = self._create_receipt(
                action=PaperAction.SUBMIT_ORDER,
                timestamp=ts,
                detail=f"Idempotent re-submission accepted for order {client_order_id}",
            )
            return existing, rcpt

        # Validate defensive universe constraints if configured (defense-in-depth)
        if (
            self._defensive_universe is not None
            and intent.target_sleeve == CapitalSleeve.BLACK_SWAN
        ):
            if intent.symbol in self._defensive_universe.core_universe_symbols:
                raise CoreDefensiveUniverseCollisionError(
                    f"Symbol '{intent.symbol}' is a Core symbol and cannot be traded defensively."
                )
            approved_syms = {s.symbol for s in self._defensive_universe.approved_instruments}
            if intent.symbol not in approved_syms:
                raise DisallowedDefensiveInstrumentError(
                    f"Symbol '{intent.symbol}' is not an approved defensive instrument."
                )

        # Validate order parameters
        if intent.quantity <= 0:
            raise InvalidPaperOrderError(f"Order quantity must be positive, got {intent.quantity}")
        if intent.limit_price is not None:
            _validate_finite_float(intent.limit_price, "limit_price", min_val=0.0001)
        if intent.stop_price is not None:
            _validate_finite_float(intent.stop_price, "stop_price", min_val=0.0001)

        # Pre-execution capital & position checks
        est_price = intent.limit_price or current_price or 0.0
        if intent.side == OrderSide.BUY:
            if est_price > 0.0:
                est_cost = intent.quantity * est_price
                if est_cost > self._cash_balance:
                    raise InsufficientPaperFundsError(
                        f"Insufficient paper cash: required ~{est_cost:.2f}, "
                        f"available {self._cash_balance:.2f}"
                    )
        elif intent.side == OrderSide.SELL:
            current_pos = self._positions.get(intent.symbol)
            held_shares = current_pos.shares if current_pos else 0
            if held_shares < intent.quantity:
                raise InsufficientPaperFundsError(
                    f"Insufficient held shares for sell order {intent.symbol}: "
                    f"required {intent.quantity}, held {held_shares}"
                )

        # Create paper order in ACCEPTED status
        paper_order = PaperOrder(
            order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            status=BrokerOrderStatus.ACCEPTED,
            filled_quantity=0,
            average_fill_price=None,
            created_at=ts,
            updated_at=ts,
            is_terminal=False,
            fills=(),
            rejection_reason=None,
        )
        self._orders[client_order_id] = paper_order

        rcpt = self._create_receipt(
            action=PaperAction.SUBMIT_ORDER,
            timestamp=ts,
            detail=(
                f"Accepted {intent.side} {intent.order_type} order {client_order_id} "
                f"for {intent.quantity} shares of {intent.symbol}"
            ),
        )
        return paper_order, rcpt

    def process_bar(
        self,
        symbol: str,
        bar: Bar,
    ) -> tuple[tuple[PaperFill, ...], PaperBrokerReceipt]:
        """Process incoming market bar, evaluating and executing resting orders deterministically.

        Evaluates MARKET, LIMIT, and STOP orders against OHLC bar boundaries and
        updates positions and account equity mark-to-market.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise UnknownPaperInputError("symbol must be a non-empty string.")
        if not isinstance(bar, Bar):
            raise UnknownPaperInputError(f"bar must be a Bar instance, got {type(bar)}")

        symbol = symbol.strip()
        bar_ts = bar.timestamp
        fills: list[PaperFill] = []

        # Find active orders for symbol
        active_orders = [
            order
            for order in self._orders.values()
            if order.symbol == symbol and not order.is_terminal
        ]

        for order in active_orders:
            fill_price: float | None = None
            slippage_val = 0.0

            if order.order_type == OrderType.MARKET:
                # Market order fills at open with simulated slippage
                slippage_factor = self._slippage_bps / 10000.0
                if order.side == OrderSide.BUY:
                    slippage_val = bar.open * slippage_factor
                    fill_price = bar.open + slippage_val
                else:
                    slippage_val = bar.open * slippage_factor
                    fill_price = bar.open - slippage_val

            elif order.order_type == OrderType.LIMIT:
                if order.limit_price is None:
                    continue
                if order.side == OrderSide.BUY:
                    # Buy limit fills if price traded down to or below limit
                    if bar.low <= order.limit_price:
                        fill_price = min(bar.open, order.limit_price)
                elif order.side == OrderSide.SELL:
                    # Sell limit fills if price traded up to or above limit
                    if bar.high >= order.limit_price:
                        fill_price = max(bar.open, order.limit_price)

            elif order.order_type == OrderType.STOP:
                if order.stop_price is None:
                    continue
                if order.side == OrderSide.BUY:
                    # Stop buy triggers on upward price break
                    if bar.high >= order.stop_price:
                        fill_price = max(bar.open, order.stop_price)
                elif order.side == OrderSide.SELL:
                    # Stop sell triggers on downward price break
                    if bar.low <= order.stop_price:
                        fill_price = min(bar.open, order.stop_price)

            if fill_price is not None:
                # Execute fill and update account/position state
                fill = self._execute_fill(
                    order=order,
                    fill_price=fill_price,
                    slippage=slippage_val,
                    timestamp=bar_ts,
                )
                if fill is not None:
                    fills.append(fill)

        # Mark-to-market all positions for symbol using bar.close
        if symbol in self._positions:
            pos = self._positions[symbol]
            mv = pos.shares * bar.close if pos.shares > 0 else 0.0
            unrealized = (
                pos.shares * (bar.close - pos.average_entry_price) if pos.shares > 0 else 0.0
            )
            self._positions[symbol] = PaperPosition(
                symbol=symbol,
                shares=pos.shares,
                average_entry_price=pos.average_entry_price,
                current_price=bar.close,
                market_value=round(mv, 4),
                unrealized_pnl=round(unrealized, 4),
                realized_pnl=round(pos.realized_pnl, 4),
                updated_at=bar_ts,
            )

        rcpt = self._create_receipt(
            action=PaperAction.PROCESS_BAR,
            timestamp=bar_ts,
            detail=(
                f"Processed bar for {symbol} at close {bar.close:.2f}: {len(fills)} fills executed"
            ),
        )
        return tuple(fills), rcpt

    def _execute_fill(
        self,
        order: PaperOrder,
        fill_price: float,
        slippage: float,
        timestamp: datetime,
    ) -> PaperFill | None:
        """Execute fill against order, mutating internal balances and positions safely."""
        fill_cost = order.quantity * fill_price

        if order.side == OrderSide.BUY:
            if fill_cost > self._cash_balance:
                # Reject order if insufficient funds at time of execution
                self._orders[order.order_id] = PaperOrder(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=order.quantity,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    status=BrokerOrderStatus.REJECTED,
                    filled_quantity=0,
                    average_fill_price=None,
                    created_at=order.created_at,
                    updated_at=timestamp,
                    is_terminal=True,
                    fills=(),
                    rejection_reason="Insufficient cash balance at execution.",
                )
                return None

            # Deduct cash
            self._cash_balance -= fill_cost

            # Update position
            existing = self._positions.get(order.symbol)
            old_shares = existing.shares if existing else 0
            old_avg = existing.average_entry_price if existing else 0.0
            old_realized = existing.realized_pnl if existing else 0.0

            new_shares = old_shares + order.quantity
            new_avg = ((old_shares * old_avg) + fill_cost) / new_shares

            self._positions[order.symbol] = PaperPosition(
                symbol=order.symbol,
                shares=new_shares,
                average_entry_price=round(new_avg, 4),
                current_price=round(fill_price, 4),
                market_value=round(new_shares * fill_price, 4),
                unrealized_pnl=round(new_shares * (fill_price - new_avg), 4),
                realized_pnl=round(old_realized, 4),
                updated_at=timestamp,
            )

        elif order.side == OrderSide.SELL:
            existing = self._positions.get(order.symbol)
            held_shares = existing.shares if existing else 0
            if held_shares < order.quantity:
                # Reject order if insufficient shares held
                self._orders[order.order_id] = PaperOrder(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=order.quantity,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    status=BrokerOrderStatus.REJECTED,
                    filled_quantity=0,
                    average_fill_price=None,
                    created_at=order.created_at,
                    updated_at=timestamp,
                    is_terminal=True,
                    fills=(),
                    rejection_reason="Insufficient held shares to sell.",
                )
                return None

            # Add proceeds to cash
            fill_proceeds = order.quantity * fill_price
            trade_realized = order.quantity * (fill_price - existing.average_entry_price)
            self._cash_balance += fill_proceeds
            self._realized_pnl += trade_realized

            remaining_shares = held_shares - order.quantity
            new_avg = existing.average_entry_price if remaining_shares > 0 else 0.0
            mv = remaining_shares * fill_price if remaining_shares > 0 else 0.0
            unrealized = remaining_shares * (fill_price - new_avg) if remaining_shares > 0 else 0.0

            self._positions[order.symbol] = PaperPosition(
                symbol=order.symbol,
                shares=remaining_shares,
                average_entry_price=round(new_avg, 4),
                current_price=round(fill_price, 4),
                market_value=round(mv, 4),
                unrealized_pnl=round(unrealized, 4),
                realized_pnl=round(existing.realized_pnl + trade_realized, 4),
                updated_at=timestamp,
            )

        # Record fill
        fill = PaperFill(
            fill_id=self._next_fill_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=round(fill_price, 4),
            slippage=round(slippage, 4),
            timestamp=timestamp,
        )

        # Update order to FILLED
        self._orders[order.order_id] = PaperOrder(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            status=BrokerOrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_fill_price=round(fill_price, 4),
            created_at=order.created_at,
            updated_at=timestamp,
            is_terminal=True,
            fills=(fill,),
            rejection_reason=None,
        )
        return fill

    def cancel_order(
        self,
        order_id: str,
        timestamp: datetime,
    ) -> tuple[PaperOrder, PaperBrokerReceipt]:
        """Cancel an active resting paper order (FR-013, INV-009)."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise UnknownPaperInputError("order_id must be a non-empty string.")
        if not isinstance(timestamp, datetime):
            raise UnknownPaperInputError("timestamp must be a datetime instance.")

        order_id = order_id.strip()
        if order_id not in self._orders:
            raise PaperOrderNotFoundError(f"Paper order '{order_id}' not found.")

        order = self._orders[order_id]
        if order.is_terminal:
            raise InvalidPaperStateError(
                f"Cannot cancel order '{order_id}' in terminal status '{order.status}'."
            )

        canceled_order = PaperOrder(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            status=BrokerOrderStatus.CANCELED,
            filled_quantity=order.filled_quantity,
            average_fill_price=order.average_fill_price,
            created_at=order.created_at,
            updated_at=timestamp,
            is_terminal=True,
            fills=order.fills,
            rejection_reason="Canceled by client request.",
        )
        self._orders[order_id] = canceled_order

        rcpt = self._create_receipt(
            action=PaperAction.CANCEL_ORDER,
            timestamp=timestamp,
            detail=f"Canceled paper order {order_id}",
        )
        return canceled_order, rcpt

    def get_orders(self) -> dict[str, PaperOrder]:
        """Return shallow copy of all tracked paper orders."""
        return dict(self._orders)

    def get_order(self, order_id: str) -> PaperOrder:
        """Lookup an internal paper order by ID."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise UnknownPaperInputError("order_id must be a non-empty string.")
        order_id = order_id.strip()
        if order_id not in self._orders:
            raise PaperOrderNotFoundError(f"Paper order '{order_id}' not found.")
        return self._orders[order_id]

    def get_position(self, symbol: str) -> PaperPosition | None:
        """Lookup an internal paper position by symbol."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise UnknownPaperInputError("symbol must be a non-empty string.")
        return self._positions.get(symbol.strip())

    # --- Authoritative Snapshot APIs (Direct Phase 3 Compatibility) ---

    def get_account_snapshot(
        self,
        timestamp: datetime | None = None,
    ) -> BrokerAccountSnapshot:
        """Return authoritative BrokerAccountSnapshot compatible with BrokerAccountReconciler."""
        ts = timestamp or self._last_timestamp
        return BrokerAccountSnapshot(
            account_id=self._account_id,
            total_equity=round(self.total_equity, 4),
            cash_balance=round(self._cash_balance, 4),
            buying_power=round(self.buying_power, 4),
            currency="USD",
            timestamp=ts,
            is_connected=True,
        )

    def get_order_snapshot(self, order_id: str) -> BrokerOrderSnapshot:
        """Return authoritative BrokerOrderSnapshot compatible with BrokerOrderReconciler."""
        order = self.get_order(order_id)
        return BrokerOrderSnapshot(
            order_id=order.order_id,
            broker_order_id=f"PAPER-{order.order_id}",
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            average_fill_price=order.average_fill_price,
            status=order.status,
            timestamp=order.updated_at,
            is_terminal=order.is_terminal,
        )

    def get_all_order_snapshots(self) -> tuple[BrokerOrderSnapshot, ...]:
        """Return authoritative snapshots for all tracked orders."""
        return tuple(self.get_order_snapshot(oid) for oid in sorted(self._orders.keys()))

    def get_position_snapshots(
        self,
        timestamp: datetime | None = None,
    ) -> tuple[BrokerPositionSnapshot, ...]:
        """Return authoritative BrokerPositionSnapshots compatible with BrokerPositionReconciler.

        Only emits positions with shares > 0 (FR-013, INV-009).
        """
        ts = timestamp or self._last_timestamp
        snapshots: list[BrokerPositionSnapshot] = []
        for sym in sorted(self._positions.keys()):
            pos = self._positions[sym]
            if pos.shares > 0:
                cost_basis = pos.shares * pos.average_entry_price
                snapshots.append(
                    BrokerPositionSnapshot(
                        symbol=pos.symbol,
                        shares=pos.shares,
                        current_price=round(pos.current_price, 4),
                        market_value=round(pos.market_value, 4),
                        cost_basis=round(cost_basis, 4),
                        timestamp=ts,
                    )
                )
        return tuple(snapshots)

    def get_account_state(
        self,
        timestamp: datetime | None = None,
    ) -> PaperAccountState:
        """Return immutable PaperAccountState snapshot."""
        ts = timestamp or self._last_timestamp
        open_pos_count = sum(1 for p in self._positions.values() if p.shares > 0)
        active_ord_count = sum(1 for o in self._orders.values() if not o.is_terminal)
        total_unrealized = sum(p.unrealized_pnl for p in self._positions.values() if p.shares > 0)
        return PaperAccountState(
            account_id=self._account_id,
            cash_balance=round(self._cash_balance, 4),
            total_equity=round(self.total_equity, 4),
            buying_power=round(self.buying_power, 4),
            currency="USD",
            realized_pnl=round(self._realized_pnl, 4),
            unrealized_pnl=round(total_unrealized, 4),
            positions_count=open_pos_count,
            active_orders_count=active_ord_count,
            timestamp=ts,
        )
