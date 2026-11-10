"""TOSO Broker Integrity Domain Models.

Immutable, deterministic data models for authoritative broker snapshots,
local account tracking, discrepancy detection, and audit reconciliation receipts.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from toso.guardian.models import CapitalSleeve, PositionRiskSnapshot


class AccountReconciliationStatus(StrEnum):
    """Machine-readable status of the account reconciliation process."""

    RECONCILED = "RECONCILED"
    DISCREPANCY_DETECTED = "DISCREPANCY_DETECTED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    FAILED_CLOSED = "FAILED_CLOSED"


class AccountDiscrepancyType(StrEnum):
    """Classification of detected account state discrepancies."""

    EQUITY_MISMATCH = "EQUITY_MISMATCH"
    CASH_MISMATCH = "CASH_MISMATCH"
    BUYING_POWER_MISMATCH = "BUYING_POWER_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    STALE_DATA = "STALE_DATA"


class AccountDiscrepancy(BaseModel):
    """Specific discrepancy between local tracking and authoritative broker state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    discrepancy_type: AccountDiscrepancyType = Field(
        ..., description="Category of the discrepancy."
    )
    local_value: float | str = Field(..., description="Value recorded in local state.")
    broker_value: float | str = Field(..., description="Value reported by authoritative broker.")
    delta: float | None = Field(
        default=None, description="Numeric difference (broker - local) when applicable."
    )
    is_material: bool = Field(
        default=True, description="Whether the difference exceeds the configured tolerance."
    )
    detail: str = Field(..., min_length=1, description="Diagnostic detail of the mismatch.")


class BrokerAccountSnapshot(BaseModel):
    """Authoritative account balance snapshot reported by the broker (FR-013, INV-009)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(..., min_length=1, description="Authoritative broker account ID.")
    total_equity: float = Field(..., gt=0.0, description="Total account portfolio equity.")
    cash_balance: float = Field(..., ge=0.0, description="Unencumbered cash balance.")
    buying_power: float = Field(..., ge=0.0, description="Available buying power.")
    currency: str = Field(default="USD", description="Base currency code.")
    timestamp: datetime = Field(..., description="Authoritative timestamp from broker feed.")
    is_connected: bool = Field(
        default=True, description="True if broker connection was live and healthy."
    )


class LocalAccountSnapshot(BaseModel):
    """Locally recorded account state tracking prior to reconciliation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(..., min_length=1, description="Local identifier for the account.")
    total_equity: float = Field(..., gt=0.0, description="Locally tracked total equity.")
    cash_balance: float = Field(..., ge=0.0, description="Locally tracked cash balance.")
    currency: str = Field(default="USD", description="Base currency code.")
    timestamp: datetime = Field(..., description="Timestamp of local record.")


class AccountReconciliationReceipt(BaseModel):
    """Immutable audit receipt issued upon account state reconciliation (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(
        ..., description="Clock anchor echoed directly from evaluation timestamp."
    )
    status: AccountReconciliationStatus = Field(
        ..., description="Overall reconciliation outcome status."
    )
    broker_account_id: str = Field(..., min_length=1, description="Authoritative broker ID.")
    authoritative_equity: float = Field(
        ..., gt=0.0, description="Authoritative total equity (broker governed, INV-009)."
    )
    authoritative_cash: float = Field(
        ..., ge=0.0, description="Authoritative cash balance (broker governed, INV-009)."
    )
    discrepancies: tuple[AccountDiscrepancy, ...] = Field(
        default_factory=tuple, description="Discrepancies identified during reconciliation."
    )
    is_synchronized: bool = Field(
        ..., description="True if local state matches broker state within tolerance."
    )
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Detailed diagnostic reasons and findings."
    )


class BrokerOrderStatus(StrEnum):
    """Authoritative lifecycle status of an order on the broker (FR-013)."""

    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    AMBIGUOUS = "AMBIGUOUS"


class OrderSide(StrEnum):
    """Order trade side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Execution type of an order."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderReconciliationStatus(StrEnum):
    """Deterministic status resulting from order reconciliation."""

    RECONCILED_MATCHED = "RECONCILED_MATCHED"
    RECONCILED_BROKER_GOVERNED = "RECONCILED_BROKER_GOVERNED"
    AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"
    DISCREPANCY_UNRESOLVABLE = "DISCREPANCY_UNRESOLVABLE"
    FAILED_CLOSED = "FAILED_CLOSED"


class BrokerOrderSnapshot(BaseModel):
    """Authoritative order state report from broker (FR-013, INV-009)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(..., min_length=1, description="Client/order tracking identifier.")
    broker_order_id: str | None = Field(
        default=None, description="Authoritative broker-assigned ID."
    )
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    side: OrderSide = Field(..., description="Order side (BUY or SELL).")
    order_type: OrderType = Field(..., description="Order execution type.")
    quantity: int = Field(..., gt=0, description="Total order share quantity.")
    filled_quantity: int = Field(default=0, ge=0, description="Cumulative executed share quantity.")
    average_fill_price: float | None = Field(
        default=None, description="Volume-weighted average fill price."
    )
    status: BrokerOrderStatus = Field(..., description="Authoritative order status from broker.")
    timestamp: datetime = Field(..., description="Authoritative broker timestamp.")
    is_terminal: bool = Field(default=False, description="True if order reached terminal state.")


class LocalOrderSnapshot(BaseModel):
    """Locally recorded order state tracking prior to reconciliation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(..., min_length=1, description="Local order tracking identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    side: OrderSide = Field(..., description="Order side (BUY or SELL).")
    order_type: OrderType = Field(..., description="Order execution type.")
    quantity: int = Field(..., gt=0, description="Total order share quantity.")
    filled_quantity: int = Field(default=0, ge=0, description="Locally tracked executed shares.")
    status: BrokerOrderStatus = Field(..., description="Locally recorded order status.")
    timestamp: datetime = Field(..., description="Timestamp of local record.")


class OrderReconciliationReceipt(BaseModel):
    """Immutable audit receipt issued upon order reconciliation (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(..., description="Clock anchor echoed from evaluation.")
    order_id: str = Field(..., min_length=1, description="Order identifier.")
    status: OrderReconciliationStatus = Field(
        ..., description="Overall order reconciliation status."
    )
    authoritative_status: BrokerOrderStatus = Field(
        ..., description="Authoritative order status (INV-009)."
    )
    authoritative_filled_quantity: int = Field(
        ..., ge=0, description="Authoritative filled shares."
    )
    authoritative_fill_price: float | None = Field(
        default=None, description="Authoritative fill price."
    )
    is_ambiguous: bool = Field(
        ..., description="True if order state cannot be confirmed (INV-010)."
    )
    can_retry: bool = Field(..., description="True if order is safe to retry/resubmit (INV-010).")
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Detailed diagnostic reasons and findings."
    )


class PositionDiscrepancyType(StrEnum):
    """Classification of detected position state discrepancies."""

    MATCHED = "MATCHED"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PHANTOM_BROKER_POSITION = "PHANTOM_BROKER_POSITION"
    GHOST_LOCAL_POSITION = "GHOST_LOCAL_POSITION"
    MARKET_VALUE_MISMATCH = "MARKET_VALUE_MISMATCH"


class PositionReconciliationStatus(StrEnum):
    """Deterministic status resulting from position reconciliation."""

    RECONCILED_CLEAN = "RECONCILED_CLEAN"
    RECONCILED_BROKER_GOVERNED = "RECONCILED_BROKER_GOVERNED"
    DISCREPANCY_DETECTED = "DISCREPANCY_DETECTED"
    FAILED_CLOSED = "FAILED_CLOSED"


class PositionDiscrepancy(BaseModel):
    """Specific discrepancy between local tracking and authoritative broker positions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    discrepancy_type: PositionDiscrepancyType = Field(
        ..., description="Category of the position discrepancy."
    )
    local_shares: int = Field(default=0, ge=0, description="Shares recorded in local state.")
    broker_shares: int = Field(default=0, ge=0, description="Shares reported by broker.")
    local_market_value: float = Field(default=0.0, ge=0.0, description="Local market value.")
    broker_market_value: float = Field(default=0.0, ge=0.0, description="Broker market value.")
    share_delta: int = Field(default=0, description="Numeric share difference (broker - local).")
    value_delta: float = Field(
        default=0.0, description="Numeric value difference (broker - local)."
    )
    is_material: bool = Field(default=True, description="Whether the difference exceeds tolerance.")
    detail: str = Field(..., min_length=1, description="Diagnostic detail of mismatch.")


class BrokerPositionSnapshot(BaseModel):
    """Authoritative open position reported by the broker (FR-013, INV-009)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Authoritative ticker symbol.")
    shares: int = Field(..., gt=0, description="Authoritative open shares on broker.")
    current_price: float = Field(..., gt=0.0, description="Authoritative market price per share.")
    market_value: float = Field(..., gt=0.0, description="Authoritative total market value.")
    cost_basis: float | None = Field(
        default=None, ge=0.0, description="Authoritative total cost basis if available."
    )
    timestamp: datetime = Field(..., description="Authoritative broker position timestamp.")


class PositionReconciliationReceipt(BaseModel):
    """Immutable audit receipt issued upon position reconciliation (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(
        ..., description="Clock anchor echoed directly from evaluation timestamp."
    )
    status: PositionReconciliationStatus = Field(
        ..., description="Overall position reconciliation outcome status."
    )
    authoritative_positions: tuple[PositionRiskSnapshot, ...] = Field(
        default_factory=tuple,
        description="Final authoritative open positions after reconciliation (INV-009).",
    )
    discrepancies: tuple[PositionDiscrepancy, ...] = Field(
        default_factory=tuple, description="Discrepancies identified during reconciliation."
    )
    has_discrepancy: bool = Field(..., description="True if any discrepancy was detected.")
    has_material_discrepancy: bool = Field(
        ..., description="True if any material discrepancy was detected."
    )
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Detailed diagnostic reasons and findings."
    )


class AmbiguityResolutionAction(StrEnum):
    """Deterministic action determined upon broker ambiguity inquiry (INV-010)."""

    ADOPT_FILL = "ADOPT_FILL"
    TRACK_ACTIVE = "TRACK_ACTIVE"
    PERMIT_RETRY = "PERMIT_RETRY"
    CONFIRM_DROPPED = "CONFIRM_DROPPED"
    HOLD_UNRESOLVED = "HOLD_UNRESOLVED"


class AmbiguityResolutionReceipt(BaseModel):
    """Immutable audit receipt issued upon order ambiguity resolution (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(
        ..., description="Clock anchor echoed directly from evaluation timestamp."
    )
    order_id: str = Field(..., min_length=1, description="Client order ID subject to resolution.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    is_resolved: bool = Field(..., description="True if ambiguity was successfully resolved.")
    action: AmbiguityResolutionAction = Field(..., description="Deterministic resolution action.")
    authoritative_order: BrokerOrderSnapshot | None = Field(
        default=None, description="Authoritative broker order snapshot if available."
    )
    can_retry: bool = Field(..., description="True if safe to retry or resubmit order (INV-010).")
    prohibit_duplicate_execution: bool = Field(
        ..., description="True if duplicate execution must be strictly prevented."
    )
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Detailed diagnostic findings and audit trail."
    )


class SubmissionDisposition(StrEnum):
    """Disposition of an order submission evaluation (Section 11.6)."""

    SUBMITTED_NEW = "SUBMITTED_NEW"
    DUPLICATE_INTERCEPTED = "DUPLICATE_INTERCEPTED"
    REJECTED_CONFLICT = "REJECTED_CONFLICT"


class OrderIntent(BaseModel):
    """Immutable request specification for proposed order submission (Section 11.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    side: OrderSide = Field(..., description="Order side (BUY or SELL).")
    order_type: OrderType = Field(..., description="Order execution type.")
    quantity: int = Field(..., gt=0, description="Order share quantity.")
    limit_price: float | None = Field(default=None, gt=0.0, description="Optional limit price.")
    stop_price: float | None = Field(
        default=None, gt=0.0, description="Optional stop trigger price."
    )
    signal_id: str = Field(..., min_length=1, description="Deterministic upstream trade signal ID.")
    intent_timestamp: datetime = Field(..., description="Timestamp anchor of trade decision.")
    client_order_id: str | None = Field(
        default=None, description="Deterministic client order ID; if None, derived from intent."
    )
    target_sleeve: CapitalSleeve = Field(
        default=CapitalSleeve.CORE, description="Target capital sleeve for order."
    )


class IdempotentSubmissionReceipt(BaseModel):
    """Immutable audit receipt issued upon order submission idempotency evaluation (INV-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(
        ..., description="Clock anchor echoed directly from evaluation timestamp."
    )
    client_order_id: str = Field(..., min_length=1, description="Authoritative client order ID.")
    idempotency_key: str = Field(..., min_length=1, description="Deterministic idempotency hash.")
    disposition: SubmissionDisposition = Field(
        ..., description="Disposition of submission attempt."
    )
    is_safe_to_submit: bool = Field(
        ..., description="True if new submission should proceed to broker."
    )
    order_intent: OrderIntent = Field(..., description="Associated immutable order intent.")
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Audit trail and diagnostics."
    )
