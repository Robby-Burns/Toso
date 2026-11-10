"""TOSO Position and Exit Domain Models.

Provides immutable, frozen Pydantic models for:
- PositionStatus and ExitReason enums.
- CoreExitOrder: Authoritative exit order payload emitted upon invalidation breach.
- CorePosition: Active trading position tracking entry, active stop, and lifecycle state.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from toso.entry.models import CoreEntryOrder, InvalidationDefinition


class PositionStatus(StrEnum):
    """Lifecycle states of a Core trading position."""

    OPEN = "OPEN"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


class ExitReason(StrEnum):
    """Deterministic classification for position exit."""

    INVALIDATION_TRIGGERED = "INVALIDATION_TRIGGERED"
    GAP_INVALIDATION_TRIGGERED = "GAP_INVALIDATION_TRIGGERED"
    STRATEGY_EXIT = "STRATEGY_EXIT"


class CoreExitOrder(BaseModel):
    """Authoritative Core exit order generated deterministically upon invalidation breach."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_order_id: str = Field(..., min_length=1, description="Unique exit order identifier.")
    position_id: str = Field(..., min_length=1, description="Originating position identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    timestamp: datetime = Field(..., description="Timestamp when exit was triggered.")
    side: str = Field(default="SELL", description="Order direction (Core longs).")
    quantity: float = Field(..., gt=0.0, description="Exact share quantity to exit.")
    trigger_price: float = Field(..., gt=0.0, description="Market price triggering the exit.")
    invalidation_price: float = Field(
        ..., gt=0.0, description="Active deterministic stop price that was breached."
    )
    exit_reason: ExitReason = Field(..., description="Deterministic exit reason.")
    explanation: str = Field(..., min_length=1, description="Deterministic rule trigger reason.")


class CorePosition(BaseModel):
    """Authoritative Core trading position governed by deterministic invalidation rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Unique position identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    entry_order: CoreEntryOrder = Field(
        ..., description="Originating entry order with recorded invalidation."
    )
    entry_price: float = Field(..., gt=0.0, description="Position execution price.")
    quantity: float = Field(..., gt=0.0, description="Position size in shares.")
    invalidation: InvalidationDefinition = Field(
        ..., description="Active deterministic invalidation definition."
    )
    status: PositionStatus = Field(
        default=PositionStatus.OPEN, description="Position lifecycle status."
    )
    opened_at: datetime = Field(..., description="Timestamp when position was opened.")
    closed_at: datetime | None = Field(
        default=None, description="Timestamp when position was closed."
    )
    pyramids: tuple[Any, ...] = Field(
        default_factory=tuple, description="Recorded pyramid additions to this position."
    )
    average_entry_price: float | None = Field(
        default=None, description="Blended weighted average cost basis across all fills."
    )
    exit_order: CoreExitOrder | None = Field(
        default=None, description="Generated exit order if breached or exiting."
    )
    realized_pnl: float | None = Field(
        default=None, description="Calculated realized PnL upon close."
    )
