"""TOSO Paper Trading Domain Models.

Immutable, deterministic data models for simulated paper trading,
order tracking, position management, and cryptographic audit receipts.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from toso.broker.models import BrokerOrderStatus, OrderSide, OrderType


class PaperAction(StrEnum):
    """Categorization of actions performed on the paper broker."""

    INITIALIZE = "INITIALIZE"
    SUBMIT_ORDER = "SUBMIT_ORDER"
    PROCESS_BAR = "PROCESS_BAR"
    CANCEL_ORDER = "CANCEL_ORDER"
    RECONCILE = "RECONCILE"


class PaperFill(BaseModel):
    """Immutable execution detail for a filled paper order (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_id: str = Field(..., min_length=1, description="Deterministic fill identifier.")
    order_id: str = Field(..., min_length=1, description="Client order ID associated with fill.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    side: OrderSide = Field(..., description="Trade side (BUY or SELL).")
    quantity: int = Field(..., gt=0, description="Executed share quantity.")
    price: float = Field(..., gt=0.0, description="Execution price per share.")
    slippage: float = Field(default=0.0, description="Simulated price slippage per share.")
    timestamp: datetime = Field(..., description="Timestamp of execution.")


class PaperOrder(BaseModel):
    """Authoritative representation of a simulated paper order (FR-013, INV-009)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(..., min_length=1, description="Deterministic unique order ID.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    side: OrderSide = Field(..., description="Order side (BUY or SELL).")
    order_type: OrderType = Field(..., description="Execution type (MARKET, LIMIT, STOP).")
    quantity: int = Field(..., gt=0, description="Total order share quantity.")
    limit_price: float | None = Field(
        default=None, gt=0.0, description="Limit price for LIMIT orders."
    )
    stop_price: float | None = Field(
        default=None, gt=0.0, description="Stop price for STOP orders."
    )
    status: BrokerOrderStatus = Field(
        default=BrokerOrderStatus.PENDING_SUBMIT,
        description="Current order status in simulated lifecycle.",
    )
    filled_quantity: int = Field(default=0, ge=0, description="Cumulative executed share quantity.")
    average_fill_price: float | None = Field(
        default=None, gt=0.0, description="Volume-weighted average fill price."
    )
    created_at: datetime = Field(..., description="Timestamp of order creation.")
    updated_at: datetime = Field(..., description="Timestamp of latest status update.")
    is_terminal: bool = Field(default=False, description="True if order reached terminal state.")
    fills: tuple[PaperFill, ...] = Field(
        default_factory=tuple, description="Execution fills recorded for this order."
    )
    rejection_reason: str | None = Field(
        default=None, description="Diagnostic reason if order was rejected."
    )


class PaperPosition(BaseModel):
    """Authoritative representation of an open paper position (FR-013, INV-009)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    shares: int = Field(default=0, ge=0, description="Open share quantity.")
    average_entry_price: float = Field(
        default=0.0, ge=0.0, description="Average entry cost per share."
    )
    current_price: float = Field(
        default=0.0, ge=0.0, description="Latest marked market price per share."
    )
    market_value: float = Field(default=0.0, ge=0.0, description="Total marked market value.")
    unrealized_pnl: float = Field(
        default=0.0, description="Unrealized profit/loss based on current price."
    )
    realized_pnl: float = Field(default=0.0, description="Cumulative realized profit/loss.")
    updated_at: datetime = Field(..., description="Timestamp of latest position update.")


class PaperAccountState(BaseModel):
    """Immutable snapshot of the paper trading account state (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(..., min_length=1, description="Unique paper account identifier.")
    cash_balance: float = Field(..., ge=0.0, description="Available cash balance.")
    total_equity: float = Field(..., ge=0.0, description="Total portfolio equity.")
    buying_power: float = Field(..., ge=0.0, description="Effective buying power.")
    currency: str = Field(default="USD", description="Account base currency.")
    realized_pnl: float = Field(default=0.0, description="Cumulative realized portfolio PnL.")
    unrealized_pnl: float = Field(default=0.0, description="Total current unrealized PnL.")
    positions_count: int = Field(default=0, ge=0, description="Count of open positions.")
    active_orders_count: int = Field(default=0, ge=0, description="Count of active resting orders.")
    timestamp: datetime = Field(..., description="Clock anchor of this account state.")


class PaperBrokerReceipt(BaseModel):
    """Immutable audit receipt issued by the paper broker engine (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(..., description="Echoed clock anchor of paper action.")
    action: PaperAction = Field(..., description="Action performed on paper broker.")
    account_id: str = Field(..., min_length=1, description="Target paper account ID.")
    total_equity: float = Field(..., ge=0.0, description="Account equity after action.")
    cash_balance: float = Field(..., ge=0.0, description="Account cash balance after action.")
    checksum: str = Field(..., min_length=64, max_length=64, description="SHA-256 integrity seal.")
    detail: str = Field(..., min_length=1, description="Summary detail of action performed.")


class PaperTradeRecord(BaseModel):
    """Immutable audit record of a completed simulated paper trade (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_id: str = Field(..., min_length=1, description="Unique trade execution identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    shares: int = Field(..., gt=0, description="Total executed share volume.")
    entry_price: float = Field(..., gt=0.0, description="Weighted average entry price.")
    exit_price: float = Field(..., gt=0.0, description="Average exit execution price.")
    entry_timestamp: datetime = Field(..., description="Timestamp of initial entry execution.")
    exit_timestamp: datetime = Field(..., description="Timestamp of exit execution.")
    pnl: float = Field(..., description="Realized net dollar profit or loss.")
    pnl_pct: float = Field(..., description="Realized return percentage.")
    exit_reason: str = Field(..., min_length=1, description="Deterministic rule triggering exit.")


class PaperSimulationStepResult(BaseModel):
    """Telemetry report produced after each discrete simulation bar step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int = Field(..., ge=0, description="Chronological index of simulation step.")
    timestamp: datetime = Field(..., description="Authoritative clock anchor of step.")
    signals: dict[str, str] = Field(
        default_factory=dict, description="Signals generated per symbol at this step."
    )
    orders_submitted: tuple[str, ...] = Field(
        default_factory=tuple, description="IDs of orders submitted during step."
    )
    fills_executed: tuple[PaperFill, ...] = Field(
        default_factory=tuple, description="Fills executed during step."
    )
    positions_open: int = Field(..., ge=0, description="Number of currently open positions.")
    cash_balance: float = Field(..., description="Cash balance after step.")
    total_equity: float = Field(..., description="Total equity mark-to-market after step.")
    is_halted: bool = Field(default=False, description="True if system halted during step.")


class PaperSimulationSummary(BaseModel):
    """Comprehensive, immutable audit summary of a complete paper simulation run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_equity: float = Field(..., gt=0.0, description="Starting portfolio equity.")
    final_equity: float = Field(..., gt=0.0, description="Ending portfolio equity.")
    total_return_pct: float = Field(..., description="Overall return percentage.")
    total_trades: int = Field(..., ge=0, description="Total round-trip trades completed.")
    winning_trades: int = Field(..., ge=0, description="Count of profitable trades.")
    losing_trades: int = Field(..., ge=0, description="Count of unprofitable trades.")
    win_rate: float = Field(..., ge=0.0, le=1.0, description="Win rate percentage.")
    max_drawdown_pct: float = Field(
        ..., ge=0.0, le=1.0, description="Maximum peak-to-trough drawdown."
    )
    completed_trades: tuple[PaperTradeRecord, ...] = Field(
        default_factory=tuple, description="History of closed round-trip trades."
    )
    receipt_checksum: str = Field(
        ..., min_length=64, max_length=64, description="Deterministic SHA-256 seal."
    )
