"""TOSO Configuration Models.

Deterministic, immutable, and versioned configuration schema for Core strategy,
risk controls, capital allocation, and universe constraints.
"""

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from toso.config.errors import ConfigurationValidationError


class ApprovalStatus(StrEnum):
    """Approval lifecycle status for configuration."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class VersionMetadata(BaseModel):
    """Configuration versioning and human-approval audit metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(..., min_length=1, description="Semantic version string, e.g. '1.0.0'")
    status: ApprovalStatus = Field(
        default=ApprovalStatus.DRAFT,
        description="Approval state. Only APPROVED configurations can execute in production.",
    )
    approved_by: str | None = Field(
        default=None,
        description="Name or ID of human owner who approved this version.",
    )
    approved_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp of when approval was recorded.",
    )
    checksum: str | None = Field(
        default=None,
        description="Cryptographic SHA-256 hash of configuration payload for audit verification.",
    )
    description: str = Field(
        default="",
        description="Human-readable description of configuration rationale or change notes.",
    )

    @model_validator(mode="after")
    def validate_approval_fields(self) -> Self:
        """Ensure that APPROVED status is accompanied by author and timestamp."""
        if self.status == ApprovalStatus.APPROVED:
            if not self.approved_by or not self.approved_by.strip():
                raise ConfigurationValidationError(
                    "APPROVED configuration must include a non-empty 'approved_by' attribute."
                )
            if not self.approved_at or not self.approved_at.strip():
                raise ConfigurationValidationError(
                    "APPROVED configuration must include a non-empty 'approved_at' timestamp."
                )
        return self


class StrategyConfig(BaseModel):
    """Core trend-following parameters and deterministic rule definitions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(..., min_length=1, description="Unique strategy identifier.")
    timeframe: str = Field(default="1d", description="Bar timeframe, e.g. '1d', '1h'.")
    fast_ma_period: int = Field(default=50, gt=0, description="Fast moving average lookback.")
    slow_ma_period: int = Field(default=200, gt=0, description="Slow moving average lookback.")
    atr_period: int = Field(default=14, gt=0, description="Average True Range period.")
    breakout_lookback_bars: int = Field(
        default=20, gt=0, description="Lookback window for Donchian breakout channel."
    )
    invalidation_atr_multiple: float = Field(
        default=2.0, gt=0.0, description="Initial stop/invalidation distance in ATR multiples."
    )
    pyramid_min_profit_r: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Minimum unrealized profit (in R-multiples) required before eligible to pyramid."
        ),
    )
    max_pyramid_adds: int = Field(
        default=3,
        ge=0,
        description="Maximum number of incremental pyramid additions to a single position.",
    )

    @model_validator(mode="after")
    def validate_moving_averages(self) -> Self:
        """Ensure fast moving average is strictly smaller than slow moving average."""
        if self.fast_ma_period >= self.slow_ma_period:
            raise ConfigurationValidationError(
                f"fast_ma_period ({self.fast_ma_period}) must be strictly less than "
                f"slow_ma_period ({self.slow_ma_period})."
            )
        return self


class RiskConfig(BaseModel):
    """Deterministic risk and sizing constraints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_risk_per_trade_pct: float = Field(
        default=0.01,
        gt=0.0,
        le=0.05,
        description="Max risk per trade as fraction of equity (e.g. 0.01 = 1%).",
    )
    max_aggregate_position_risk_pct: float = Field(
        default=0.02,
        gt=0.0,
        le=0.10,
        description="Max aggregate risk on a position with pyramids (e.g. 0.02 = 2%).",
    )
    max_portfolio_risk_pct: float = Field(
        default=0.06,
        gt=0.0,
        le=0.20,
        description="Max aggregate risk across all open positions (e.g. 0.06 = 6%).",
    )
    max_position_concentration_pct: float = Field(
        default=0.20,
        gt=0.0,
        le=0.35,
        description="Intake concentration cap for a single position market value / total equity.",
    )
    slippage_buffer_pct: float = Field(
        default=0.005,
        ge=0.0,
        le=0.05,
        description="Explicit gap/slippage risk buffer factored into risk calculation.",
    )
    emergency_drawdown_stop_pct: float = Field(
        default=0.10,
        gt=0.0,
        le=0.30,
        description="Peak-to-trough portfolio drawdown threshold triggering HALTED state.",
    )

    @model_validator(mode="after")
    def validate_risk_hierarchy(self) -> Self:
        """Ensure aggregate position risk is at least single trade risk, and within portfolio."""
        if self.max_aggregate_position_risk_pct < self.max_risk_per_trade_pct:
            raise ConfigurationValidationError(
                f"max_aggregate_position_risk_pct ({self.max_aggregate_position_risk_pct}) "
                f"cannot be less than max_risk_per_trade_pct ({self.max_risk_per_trade_pct})."
            )
        if self.max_portfolio_risk_pct < self.max_aggregate_position_risk_pct:
            raise ConfigurationValidationError(
                f"max_portfolio_risk_pct ({self.max_portfolio_risk_pct}) cannot be "
                f"less than max_aggregate_position_risk_pct "
                f"({self.max_aggregate_position_risk_pct})."
            )
        return self


class CapitalAllocationConfig(BaseModel):
    """Enforces strict capital separation between Core Trader, Reserve, and Black Swan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    core_allocation_pct: float = Field(
        default=0.80,
        gt=0.0,
        lt=1.0,
        description="Portion of portfolio capital dedicated to Core trend-following.",
    )
    reserve_allocation_pct: float = Field(
        default=0.15,
        ge=0.0,
        lt=1.0,
        description="Portion of portfolio capital held in cash/reserve.",
    )
    black_swan_allocation_pct: float = Field(
        default=0.05,
        ge=0.0,
        lt=1.0,
        description="Portion of portfolio capital allocated to Black Swan defense.",
    )

    @model_validator(mode="after")
    def validate_allocation_sum(self) -> Self:
        """Ensure capital allocations sum to 100% (1.0)."""
        total = (
            self.core_allocation_pct + self.reserve_allocation_pct + self.black_swan_allocation_pct
        )
        if not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise ConfigurationValidationError(
                f"Capital allocations must sum to 1.0 (100%), got {total:.4f} "
                f"(core={self.core_allocation_pct}, reserve={self.reserve_allocation_pct}, "
                f"black_swan={self.black_swan_allocation_pct})."
            )
        return self


class UniverseConfig(BaseModel):
    """Eligibility criteria and allowed symbol universe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible_symbols: tuple[str, ...] = Field(
        default=(),
        description="Explicit list of permitted stock tickers.",
    )
    min_average_daily_volume_shares: int = Field(
        default=500_000,
        ge=0,
        description="Minimum 20-day average volume required for liquidity.",
    )
    min_share_price_dollars: float = Field(
        default=5.0,
        ge=0.0,
        description="Minimum share price to avoid penny stock anomalies.",
    )


class CoreConfig(BaseModel):
    """Root configuration contract for TOSO Core trend-following and risk systems."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: VersionMetadata
    strategy: StrategyConfig
    risk: RiskConfig
    capital: CapitalAllocationConfig
    universe: UniverseConfig
    require_invalidation_before_entry: bool = Field(
        default=True,
        description="Permanent invariant: entry requires a recorded invalidation price.",
    )
    short_selling_enabled: bool = Field(
        default=False,
        description="Whether live shorting is enabled (defaults to False until Phase 4).",
    )

    @model_validator(mode="after")
    def validate_root_invariants(self) -> Self:
        """Validate core invariants that must never be disabled."""
        if not self.require_invalidation_before_entry:
            raise ConfigurationValidationError(
                "require_invalidation_before_entry must remain True (permanent invariant INV-005)."
            )
        return self
