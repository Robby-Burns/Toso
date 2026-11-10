"""TOSO Black Swan Defense Domain Models.

Immutable, deterministic data models for Black Swan capital partitioning,
capacity tracking, and defensive intake gating receipts (FR-010, FR-011, INV-013).
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BlackSwanIntakeStatus(StrEnum):
    """Machine-readable status of the Black Swan defensive intake gating evaluation."""

    APPROVED = "APPROVED"
    CAPITAL_EXHAUSTED = "CAPITAL_EXHAUSTED"
    SLEEVE_BREACH = "SLEEVE_BREACH"
    FAILED_CLOSED = "FAILED_CLOSED"


class BlackSwanCapitalSnapshot(BaseModel):
    """Authoritative snapshot of Black Swan defensive capital allocation and capacity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    portfolio_equity: float = Field(..., gt=0.0, description="Total portfolio equity anchor basis.")
    allocation_pct: float = Field(
        ..., gt=0.0, lt=1.0, description="Configured defensive capital allocation percentage."
    )
    allocated_budget_dollars: float = Field(
        ..., gt=0.0, description="Total dollar budget allocated to Black Swan defense."
    )
    deployed_market_value_dollars: float = Field(
        default=0.0, ge=0.0, description="Current market value deployed in defensive assets."
    )
    available_capacity_dollars: float = Field(
        ..., ge=0.0, description="Remaining available uncommitted defensive capital."
    )
    timestamp: datetime = Field(..., description="Snapshot as-of timestamp providing clock anchor.")


class BlackSwanCapitalAssessment(BaseModel):
    """Immutable audit receipt issued upon defensive capital intake evaluation (INV-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: str = Field(..., min_length=1, description="Deterministic unique assessment ID.")
    timestamp: datetime = Field(
        ..., description="Clock anchor echoed directly from evaluation timestamp."
    )
    status: BlackSwanIntakeStatus = Field(
        ..., description="Gating outcome for defensive expenditure."
    )
    capital_snapshot: BlackSwanCapitalSnapshot = Field(
        ..., description="State of Black Swan defensive capital."
    )
    proposed_market_value: float = Field(
        ..., ge=0.0, description="Proposed new defensive allocation market value."
    )
    is_approved: bool = Field(
        ..., description="True if proposed expenditure is approved within defensive budget."
    )
    diagnostic_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Detailed diagnostic reasons and audit trail."
    )


class DefensiveInstrumentType(StrEnum):
    """Permitted asset class taxonomy for Black Swan defensive instruments."""

    INVERSE_INDEX_ETF = "INVERSE_INDEX_ETF"
    LEVERAGED_INVERSE_ETF = "LEVERAGED_INVERSE_ETF"
    VOLATILITY_ETP = "VOLATILITY_ETP"
    CASH_EQUIVALENT = "CASH_EQUIVALENT"


class DefensiveInstrumentSpec(BaseModel):
    """Specification of an approved defensive instrument."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Defensive asset ticker symbol.")
    instrument_type: DefensiveInstrumentType = Field(
        ..., description="Taxonomy classification of defensive asset."
    )
    underlying_index: str = Field(
        ..., min_length=1, description="Underlying benchmark/target (e.g. S&P 500, VIX Futures)."
    )
    leverage_factor: float = Field(
        ..., description="Leverage exposure multiple (e.g., -1.0 short, -3.0 leveraged, +1.0 cash)."
    )
    max_allocation_of_sleeve_pct: float = Field(
        ..., gt=0.0, le=1.0, description="Max proportion of the Black Swan defensive sleeve budget."
    )
    max_holding_period_days: int | None = Field(
        default=None, gt=0, description="Max days to hold before de-escalation to limit decay."
    )
    min_average_daily_volume: int = Field(
        default=100000, ge=0, description="Minimum ADV required for sufficient defensive liquidity."
    )


class DefensiveUniverseConfig(BaseModel):
    """Authoritative universe configuration and bounds for defensive instruments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved_instruments: tuple[DefensiveInstrumentSpec, ...] = Field(
        ..., description="List of pre-approved defensive instrument specifications."
    )
    core_universe_symbols: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Active Core universe symbols to prevent cross-sleeve collisions (FR-011).",
    )
    min_share_price_dollars: float = Field(
        default=1.0, gt=0.0, description="Minimum asset share price threshold."
    )


class DefensiveEligibilityStatus(StrEnum):
    """Outcome status for defensive instrument eligibility evaluation."""

    ELIGIBLE = "ELIGIBLE"
    UNAPPROVED_SYMBOL = "UNAPPROVED_SYMBOL"
    UNIVERSE_COLLISION = "UNIVERSE_COLLISION"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    FAILED_CLOSED = "FAILED_CLOSED"


class DefensiveEligibilityReceipt(BaseModel):
    """Immutable audit receipt issued upon defensive instrument evaluation (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    symbol: str = Field(..., min_length=1, description="Evaluated ticker symbol.")
    status: DefensiveEligibilityStatus = Field(
        ..., description="Defensive eligibility outcome status."
    )
    is_eligible: bool = Field(..., description="True if instrument is approved and safe to deploy.")
    instrument_spec: DefensiveInstrumentSpec | None = Field(
        default=None, description="Defensive spec if symbol is approved."
    )
    reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Audit and diagnostic messages."
    )
    evaluation_timestamp: datetime = Field(
        ..., description="Echoed evaluation timestamp (INV-001)."
    )


class DefensiveTriggerType(StrEnum):
    """Classification of systemic risk trigger sources for defensive action."""

    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    BENCHMARK_BREAKDOWN = "BENCHMARK_BREAKDOWN"
    PORTFOLIO_DRAWDOWN_WARNING = "PORTFOLIO_DRAWDOWN_WARNING"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class DefensiveTriggerCondition(BaseModel):
    """Specification of an approved deterministic trigger threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_type: DefensiveTriggerType = Field(..., description="Category of trigger.")
    threshold_value: float = Field(
        ..., description="Numeric threshold (e.g. VIX level, % drawdown)."
    )
    lookback_bars: int = Field(
        default=1, ge=1, description="Number of bars over which condition is evaluated."
    )
    description: str = Field(default="", description="Human-readable context of condition.")


class DefensiveTriggerSignal(BaseModel):
    """Deterministic signal emitted when systemic crisis conditions are detected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str = Field(..., min_length=1, description="Deterministic unique signal ID.")
    trigger_type: DefensiveTriggerType = Field(..., description="Active trigger classification.")
    is_triggered: bool = Field(..., description="True if condition threshold is breached.")
    observed_value: float = Field(..., description="Actual observed metric value.")
    threshold_value: float = Field(..., description="Configured trigger threshold.")
    severity_level: int = Field(
        default=1, ge=1, le=5, description="Crisis severity scale from 1 (mild) to 5 (extreme)."
    )
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp.")
    rationale: str = Field(default="", description="Audit reason for signal emission.")


class DefensiveSizingReceipt(BaseModel):
    """Immutable audit receipt issued upon defensive position sizing calculation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    symbol: str = Field(..., min_length=1, description="Target defensive ticker symbol.")
    shares: int = Field(..., ge=0, description="Allocated whole-share position size.")
    share_price: float = Field(..., gt=0.0, description="Reference share price.")
    slippage_buffer_pct: float = Field(
        ..., ge=0.0, description="Explicit gap/slippage allowance pct."
    )
    effective_share_price: float = Field(
        ..., gt=0.0, description="Price adjusted upward for slippage."
    )
    allocated_budget_dollars: float = Field(
        ..., ge=0.0, description="Total Black Swan sleeve capital."
    )
    available_capacity_dollars: float = Field(
        ..., ge=0.0, description="Remaining uncommitted defensive capital."
    )
    sub_allocation_cap_dollars: float = Field(
        ..., ge=0.0, description="Instrument-specific sub-allocation dollar cap."
    )
    actual_cost_dollars: float = Field(
        ..., ge=0.0, description="Calculated total purchase cost including slippage."
    )
    remaining_capacity_dollars: float = Field(
        ..., ge=0.0, description="Remaining uncommitted capital after sizing."
    )
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp.")
    rationale: str = Field(default="", description="Audit justification for sizing decision.")


class DefensiveExitReason(StrEnum):
    """Categorization of rationales for defensive position de-escalation."""

    HOLDING_PERIOD_EXPIRED = "HOLDING_PERIOD_EXPIRED"
    NORMALIZATION_SIGNAL = "NORMALIZATION_SIGNAL"
    PROFIT_TARGET_MET = "PROFIT_TARGET_MET"
    MANUAL_DEESCALATION = "MANUAL_DEESCALATION"
    NONE = "NONE"


class DefensiveExitAction(StrEnum):
    """Prescribed liquidation action for an active defensive position."""

    FULL_LIQUIDATION = "FULL_LIQUIDATION"
    PARTIAL_TRIM = "PARTIAL_TRIM"
    HOLD = "HOLD"


class DefensivePosition(BaseModel):
    """Active open defensive position tracked in the Black Swan sleeve."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Deterministic position identifier.")
    symbol: str = Field(..., min_length=1, description="Defensive asset ticker symbol.")
    shares: int = Field(..., gt=0, description="Current share quantity held.")
    entry_price: float = Field(..., gt=0.0, description="Execution share price at entry.")
    entry_timestamp: datetime = Field(..., description="Timestamp when position was entered.")
    current_price: float = Field(..., gt=0.0, description="Latest market price.")


class DefensiveExitReceipt(BaseModel):
    """Immutable audit receipt issued upon defensive exit / de-escalation evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    position_id: str = Field(..., min_length=1, description="Evaluated defensive position ID.")
    symbol: str = Field(..., min_length=1, description="Defensive asset ticker symbol.")
    action: DefensiveExitAction = Field(..., description="Evaluated exit action.")
    reason: DefensiveExitReason = Field(..., description="Specific trigger/decay exit reason.")
    shares_to_exit: int = Field(..., ge=0, description="Whole shares to liquidate.")
    remaining_shares: int = Field(..., ge=0, description="Remaining shares after exit.")
    exit_price: float = Field(..., gt=0.0, description="Reference exit execution price.")
    estimated_proceeds_dollars: float = Field(
        ..., ge=0.0, description="Gross cash proceeds returning to defensive capacity."
    )
    holding_days: int = Field(..., ge=0, description="Calendar days elapsed since entry.")
    evaluation_timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
    rationale: str = Field(default="", description="Audit reason for de-escalation decision.")


class DefensiveDeescalationPlan(BaseModel):
    """Comprehensive plan summarizing all defensive de-escalation exits and returned capital."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(..., min_length=1, description="Deterministic plan identifier.")
    exit_receipts: tuple[DefensiveExitReceipt, ...] = Field(
        default_factory=tuple, description="Individual exit receipts."
    )
    total_proceeds_replenishment_dollars: float = Field(
        ..., ge=0.0, description="Total capital returning to available Black Swan capacity."
    )
    evaluation_timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
