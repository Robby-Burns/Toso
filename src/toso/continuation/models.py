"""TOSO Winner Continuation & Anti-Take-Profit Domain Models.

Provides frozen, immutable Pydantic models for:
- ContinuationStatus: Running winner, standard hold, or invalidation pending.
- ExitType: Categorization of approved vs prohibited exit requests.
- WinnerContinuationDecision: Audit receipt evaluating winner holding & concentration intake.
- ExitValidationReceipt: Receipt validating exit requests against anti-take-profit rules.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ContinuationStatus(StrEnum):
    """Lifecycle classification of position continuation status."""

    RUNNING_WINNER = "RUNNING_WINNER"
    STANDARD_HOLD = "STANDARD_HOLD"
    INVALIDATION_PENDING = "INVALIDATION_PENDING"


class ExitType(StrEnum):
    """Categorization of proposed exit types (approved vs prohibited)."""

    # Approved deterministic exit types
    INVALIDATION_STOP = "INVALIDATION_STOP"
    GAP_INVALIDATION_STOP = "GAP_INVALIDATION_STOP"
    STRATEGY_SIGNAL_EXIT = "STRATEGY_SIGNAL_EXIT"

    # Prohibited exit types (Philosophy §2.2, §2.6, FR-007, FR-008)
    FIXED_PROFIT_TARGET = "FIXED_PROFIT_TARGET"
    PROFIT_TRIM = "PROFIT_TRIM"
    CONCENTRATION_REBALANCE = "CONCENTRATION_REBALANCE"
    DISCRETIONARY_EXIT = "DISCRETIONARY_EXIT"


class WinnerContinuationDecision(BaseModel):
    """Immutable audit receipt of deterministic winner continuation evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Position identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    timestamp: datetime = Field(..., description="Timestamp of evaluation.")
    current_price: float = Field(..., gt=0.0, description="Current market price.")
    cost_basis: float = Field(
        ..., gt=0.0, description="Effective cost basis (blended VWAP if pyramids exist)."
    )
    initial_risk_dollars: float = Field(
        ..., ge=0.0, description="Initial dollar risk anchored to initial entry order."
    )
    unrealized_profit_dollars: float = Field(
        ..., description="Mark-to-market unrealized profit in dollars."
    )
    unrealized_profit_r: float = Field(
        ..., description="Unrealized profit in R-multiples relative to initial risk."
    )
    position_weight_pct: float = Field(
        ..., ge=0.0, description="Current market value / portfolio equity fraction."
    )
    max_concentration_pct: float = Field(
        ..., gt=0.0, description="Intake concentration limit from risk config."
    )
    is_concentration_breached: bool = Field(
        ..., description="Whether position weight exceeds intake cap (informational only)."
    )
    continuation_status: ContinuationStatus = Field(
        ..., description="Position continuation classification."
    )
    is_continuation_permitted: bool = Field(
        ..., description="Whether position is permitted to continue running without exit."
    )
    action: str = Field(
        default="CONTINUE_HOLD", description="Action: CONTINUE_HOLD or EXIT_REQUIRED."
    )
    explanation: str = Field(..., min_length=1, description="Detailed audit rationale.")


class ExitValidationReceipt(BaseModel):
    """Immutable receipt validating a proposed exit request against anti-take-profit rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Position identifier.")
    exit_type: ExitType = Field(..., description="Proposed exit type.")
    requester_id: str = Field(
        default="System", description="Entity requesting the exit (for audit trail)."
    )
    is_permitted: bool = Field(..., description="Whether the exit request is approved.")
    timestamp: datetime = Field(..., description="Validation timestamp.")
    explanation: str = Field(..., min_length=1, description="Validation rationale.")
