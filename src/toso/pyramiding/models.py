"""TOSO Winner-Only Pyramiding Domain Models.

Frozen, immutable data models representing pyramid orders,
winner eligibility evaluations, and aggregate position risk assessments.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from toso.entry.models import InvalidationDefinition


class PyramidOrder(BaseModel):
    """Immutable record representing an approved pyramid addition order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pyramid_order_id: str = Field(..., min_length=1, description="Unique pyramid order ID.")
    position_id: str = Field(..., min_length=1, description="Target Core position identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    timestamp: datetime = Field(..., description="Timestamp of pyramid creation.")
    side: str = Field(default="BUY", description="Trade direction (Core longs).")
    add_index: int = Field(..., ge=1, description="1-based sequential addition index.")
    price: float = Field(..., gt=0.0, description="Execution or reference add price.")
    quantity: int = Field(..., ge=1, description="Discrete integer share quantity added.")
    new_invalidation: InvalidationDefinition = Field(
        ..., description="Mandatory tighter/ratcheted stop protecting the aggregate position."
    )
    explanation: str = Field(
        ..., min_length=1, description="Deterministic audit rationale for pyramid addition."
    )


class PyramidEligibility(BaseModel):
    """Deterministic evaluation record of a position's qualification to pyramid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Target Core position identifier.")
    is_eligible: bool = Field(
        ..., description="Whether position satisfies all deterministic winner conditions."
    )
    current_price: float = Field(..., gt=0.0, description="Current market reference price.")
    initial_risk_dollars: float = Field(
        ..., ge=0.0, description="Base 1R dollar risk anchored to initial entry."
    )
    unrealized_profit_dollars: float = Field(
        ..., description="Unrealized mark-to-market profit measured against cost basis."
    )
    r_multiple: float = Field(
        ..., description="Unrealized profit expressed as a multiple of initial 1R risk."
    )
    current_adds_count: int = Field(
        ..., ge=0, description="Number of existing pyramid additions on the position."
    )
    max_adds_allowed: int = Field(
        ..., ge=0, description="Maximum permitted pyramid additions from StrategyConfig."
    )
    rejection_reason: str | None = Field(
        default=None, description="Deterministic reason if position is not eligible to pyramid."
    )


class AggregateRiskAssessment(BaseModel):
    """Assessment of aggregate position and portfolio risk resulting from a proposed addition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Target Core position identifier.")
    total_shares: int = Field(..., ge=1, description="Combined shares (existing + proposed add).")
    weighted_average_entry: float = Field(
        ..., gt=0.0, description="Blended cost basis across all fills."
    )
    new_stop_price: float = Field(
        ..., gt=0.0, description="Active stop protecting the combined position."
    )
    nominal_aggregate_risk_dollars: float = Field(
        ..., ge=0.0, description="Nominal dollar loss if stopped out at new_stop_price."
    )
    effective_aggregate_risk_dollars: float = Field(
        ..., ge=0.0, description="Effective dollar loss factoring in the slippage buffer."
    )
    aggregate_risk_pct_equity: float = Field(
        ..., ge=0.0, description="Aggregate effective risk as fraction of portfolio equity."
    )
    resulting_market_value: float = Field(
        ..., gt=0.0, description="Total market value of combined position."
    )
    resulting_concentration_pct: float = Field(
        ..., ge=0.0, description="Total position market value as fraction of portfolio equity."
    )
