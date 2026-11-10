"""TOSO Entry and Invalidation Domain Models.

Provides frozen, immutable, and strictly validated models for:
- InvalidationDefinition: Deterministic stop/invalidation metadata and price.
- GapSlippageRiskModel: Explicit risk accounting factoring slippage buffer and adverse gaps.
- CoreEntryOrder: Authoritative Core entry structure requiring invalidation before execution.
"""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from toso.entry.errors import (
    InvalidEntryOrderError,
    InvalidInvalidationError,
    MissingInvalidationError,
)


class InvalidationDefinition(BaseModel):
    """Immutable, deterministic invalidation definition established before execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: float = Field(..., gt=0.0, description="Deterministic stop/invalidation price.")
    invalidation_type: str = Field(default="ATR_STOP", description="Rule type identifier.")
    atr_multiple: float = Field(..., gt=0.0, description="ATR multiplier used in formula.")
    reference_atr: float = Field(..., gt=0.0, description="Reference ATR at calculation time.")
    established_at: datetime = Field(
        ..., description="Timestamp when invalidation was established."
    )
    is_ratcheted: bool = Field(
        default=False, description="Whether stop has been ratcheted tighter."
    )
    reason: str = Field(..., min_length=1, description="Deterministic calculation rationale.")

    @model_validator(mode="before")
    @classmethod
    def validate_price_positive(cls, data: Any) -> Any:
        """Ensure invalidation price is strictly positive before Pydantic coercion."""
        if isinstance(data, dict) and "price" in data:
            price = data.get("price")
            if price is not None and price <= 0:
                raise InvalidInvalidationError(f"Invalidation price must be positive, got {price}.")
        return data


class GapSlippageRiskModel(BaseModel):
    """Explicit risk model incorporating execution slippage and adverse gap accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nominal_risk_per_share: float = Field(
        ...,
        ge=0.0,
        description="Nominal capital risk distance: max(0.0, entry_price - invalidation_price).",
    )
    slippage_buffer_pct: float = Field(
        ..., ge=0.0, description="Configured slippage buffer fraction (e.g. 0.005 for 0.5%)."
    )
    slippage_allowance_per_share: float = Field(
        ...,
        ge=0.0,
        description="Monetary allowance for execution slippage: entry_price * slippage_buffer_pct.",
    )
    effective_risk_per_share: float = Field(
        ..., ge=0.0, description="Effective risk per share: nominal_risk + slippage_allowance."
    )

    @classmethod
    def compute(
        cls, entry_price: float, invalidation_price: float, slippage_buffer_pct: float
    ) -> "GapSlippageRiskModel":
        """Compute the gap/slippage risk accounting deterministically."""
        nominal_risk = max(0.0, round(entry_price - invalidation_price, 4))
        slippage_allowance = round(entry_price * slippage_buffer_pct, 4)
        effective_risk = round(nominal_risk + slippage_allowance, 4)
        return cls(
            nominal_risk_per_share=nominal_risk,
            slippage_buffer_pct=slippage_buffer_pct,
            slippage_allowance_per_share=slippage_allowance,
            effective_risk_per_share=effective_risk,
        )

    def calculate_gap_loss(self, entry_price: float, gap_down_pct: float) -> float:
        """Calculate realized loss per share if an adverse gap opens down by gap_down_pct.

        Demonstrates that invalidation is not assumed to be a guaranteed cash stop.
        """
        gap_loss = round(entry_price * gap_down_pct, 4)
        return max(gap_loss, self.nominal_risk_per_share)


class CoreEntryOrder(BaseModel):
    """Authoritative Core entry order requiring recorded invalidation before execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(..., min_length=1, description="Unique order identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    timestamp: datetime = Field(..., description="Order creation timestamp.")
    side: str = Field(default="BUY", description="Trade side (Core longs).")
    entry_price: float = Field(..., gt=0.0, description="Intended reference entry price.")
    invalidation: InvalidationDefinition = Field(
        ..., description="Required deterministic invalidation definition."
    )
    risk_accounting: GapSlippageRiskModel = Field(
        ..., description="Gap and slippage risk accounting."
    )
    signal_id: str | None = Field(
        default=None, description="Initiating TradeSignal ID if applicable."
    )
    reason: str = Field(..., min_length=1, description="Deterministic rule trigger reason.")

    @model_validator(mode="before")
    @classmethod
    def validate_invalidation_presence(cls, data: Any) -> Any:
        """Ensure invalidation is explicitly provided before Pydantic type checks."""
        if isinstance(data, dict):
            if "invalidation" not in data or data.get("invalidation") is None:
                raise MissingInvalidationError(
                    "CoreEntryOrder requires a recorded InvalidationDefinition before execution."
                )
            if "entry_price" in data:
                entry_price = data.get("entry_price")
                if entry_price is not None and entry_price <= 0:
                    raise InvalidEntryOrderError(
                        f"Entry price must be positive, got {entry_price}."
                    )
        return data

    @model_validator(mode="after")
    def validate_invalidation_boundaries(self) -> Self:
        """Ensure invalidation price respects price boundary invariants relative to entry."""
        if not self.invalidation.is_ratcheted and self.invalidation.price >= self.entry_price:
            raise InvalidInvalidationError(
                f"Long entry invalidation price ({self.invalidation.price}) cannot be greater than "
                f"or equal to entry price ({self.entry_price})."
            )
        if self.invalidation.price <= 0:
            raise InvalidInvalidationError(
                f"Invalidation price must be positive, got {self.invalidation.price}."
            )
        return self
