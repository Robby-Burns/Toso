"""TOSO Position Sizing Domain Models.

Frozen, immutable data models representing deterministic sizing constraints,
overnight gap scenario stress calculations, and sizing decisions.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SizingConstraint(StrEnum):
    """Enumeration of constraints that can bind position sizing."""

    RISK_BUDGET = "RISK_BUDGET"
    CONCENTRATION_LIMIT = "CONCENTRATION_LIMIT"
    CORE_CAPITAL_LIMIT = "CORE_CAPITAL_LIMIT"


class GapScenarioLoss(BaseModel):
    """Scenario stress loss modeling an adverse gap opening down beyond invalidation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_down_pct: float = Field(
        ..., gt=0.0, description="Adverse gap drop fraction (e.g. 0.05 for 5%)."
    )
    loss_per_share: float = Field(
        ..., ge=0.0, description="Realized loss per share under gap scenario."
    )
    total_scenario_loss_dollars: float = Field(
        ..., ge=0.0, description="Total position loss in dollars under gap scenario."
    )
    scenario_loss_pct_equity: float = Field(
        ..., ge=0.0, description="Total gap loss expressed as percentage of portfolio equity."
    )


class PositionSizingDecision(BaseModel):
    """Deterministic, immutable position sizing decision record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    timestamp: datetime = Field(..., description="Timestamp of sizing calculation.")
    portfolio_equity: float = Field(..., gt=0.0, description="Total portfolio equity reference.")
    risk_budget_dollars: float = Field(
        ..., ge=0.0, description="Target or available dollar risk allocated to trade."
    )
    entry_price: float = Field(..., gt=0.0, description="Intended reference entry price.")
    invalidation_price: float = Field(..., gt=0.0, description="Deterministic invalidation price.")
    nominal_risk_per_share: float = Field(
        ..., ge=0.0, description="Nominal stop distance (entry - invalidation)."
    )
    slippage_buffer_pct: float = Field(
        ..., ge=0.0, description="Configured slippage buffer fraction."
    )
    effective_risk_per_share: float = Field(
        ..., gt=0.0, description="Effective risk per share incorporating slippage buffer."
    )
    allocated_shares: int = Field(..., ge=1, description="Executable integer share quantity.")
    market_value: float = Field(
        ..., gt=0.0, description="Total initial market value of position (shares * entry_price)."
    )
    nominal_risk_dollars: float = Field(
        ..., ge=0.0, description="Nominal capital risk in dollars (shares * nominal_risk)."
    )
    effective_risk_dollars: float = Field(
        ...,
        ge=0.0,
        description="Effective capital risk in dollars (shares * effective_risk_per_share).",
    )
    concentration_pct: float = Field(
        ..., ge=0.0, description="Position market value as fraction of portfolio equity."
    )
    binding_constraint: SizingConstraint = Field(
        ..., description="The limiting constraint that determined final allocated shares."
    )
    gap_scenarios: tuple[GapScenarioLoss, ...] = Field(
        default_factory=tuple,
        description="Adverse gap scenario stress test calculations.",
    )
    explanation: str = Field(
        ..., min_length=1, description="Deterministic audit rationale explaining sizing derivation."
    )
