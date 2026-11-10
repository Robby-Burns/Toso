"""TOSO Guardian Domain Models.

Immutable, deterministic data models for portfolio-level risk evaluation,
position risk snapshots, proposed exposures, and audit assessment receipts.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskStatus(StrEnum):
    """Operational risk status of the portfolio."""

    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    BREACH_BLOCKED = "BREACH_BLOCKED"
    HALTED = "HALTED"


class CapitalSleeve(StrEnum):
    """Capital sleeve classification for portfolio partitioning."""

    CORE = "CORE"
    RESERVE = "RESERVE"
    BLACK_SWAN = "BLACK_SWAN"


class CapitalSleeveStatus(StrEnum):
    """Machine-readable intake gating decision status for capital sleeve allocation."""

    APPROVED = "APPROVED"
    BLOCKED_CORE_EXHAUSTED = "BLOCKED_CORE_EXHAUSTED"
    BLOCKED_RESERVE_ENCROACHMENT = "BLOCKED_RESERVE_ENCROACHMENT"
    BLOCKED_BLACK_SWAN_ENCROACHMENT = "BLOCKED_BLACK_SWAN_ENCROACHMENT"
    BLOCKED_UNKNOWN_INPUT = "BLOCKED_UNKNOWN_INPUT"


class ProposedExposure(BaseModel):
    """Proposed new exposure intake (initial entry or pyramid addition).

    Strictly intake-only: additional_shares must be strictly positive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    is_pyramid: bool = Field(
        default=False, description="Whether this exposure is an addition to an existing position."
    )
    additional_shares: int = Field(
        ..., gt=0, description="Number of additional shares requested (strictly positive)."
    )
    entry_price: float = Field(..., gt=0.0, description="Execution/entry price per share.")
    stop_loss_price: float = Field(..., gt=0.0, description="Protective stop price per share.")
    effective_risk_dollars: float = Field(
        ..., gt=0.0, description="Effective dollar risk of the proposed addition."
    )
    effective_market_value: float = Field(
        ..., gt=0.0, description="Effective market value (notional) of the proposed addition."
    )
    target_sleeve: CapitalSleeve = Field(
        default=CapitalSleeve.CORE, description="Target capital sleeve for the proposed exposure."
    )


class PositionRiskSnapshot(BaseModel):
    """Current risk snapshot for an open position."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    shares: int = Field(..., gt=0, description="Current open shares.")
    current_price: float = Field(..., gt=0.0, description="Current market price per share.")
    stop_loss_price: float = Field(..., gt=0.0, description="Current protective stop price.")
    open_risk_dollars: float = Field(
        ..., ge=0.0, description="Current open dollar risk based on active stop."
    )
    market_value: float = Field(..., gt=0.0, description="Current market value (shares * price).")
    sleeve: CapitalSleeve = Field(
        default=CapitalSleeve.CORE, description="Capital sleeve to which this position is assigned."
    )


class PortfolioStateSnapshot(BaseModel):
    """Authoritative snapshot of portfolio state and open positions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    portfolio_equity: float = Field(..., gt=0.0, description="Current total portfolio equity.")
    peak_portfolio_equity: float = Field(
        ..., gt=0.0, description="High-water mark / peak portfolio equity."
    )
    cash_balance: float = Field(..., ge=0.0, description="Available unencumbered cash balance.")
    positions: tuple[PositionRiskSnapshot, ...] = Field(
        default_factory=tuple, description="Active open position risk snapshots."
    )
    timestamp: datetime = Field(
        ..., description="Snapshot as-of timestamp providing authoritative clock anchor."
    )


class PortfolioRiskAssessment(BaseModel):
    """Immutable audit receipt produced by the Guardian portfolio risk evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RiskStatus = Field(..., description="Overall risk status category.")
    is_approved: bool = Field(
        ..., description="True if proposed exposure complies with all risk constraints."
    )
    current_heat_pct: float = Field(
        ..., ge=0.0, description="Current total open risk as fraction of equity."
    )
    post_heat_pct: float = Field(
        ..., ge=0.0, description="Projected total open risk if proposed exposure is accepted."
    )
    max_portfolio_risk_pct: float = Field(
        ..., gt=0.0, description="Configured portfolio heat ceiling."
    )
    current_drawdown_pct: float = Field(
        ..., ge=0.0, description="Current peak-to-trough equity drawdown."
    )
    emergency_drawdown_stop_pct: float = Field(
        ..., gt=0.0, description="Configured emergency drawdown halt threshold."
    )
    current_gross_exposure_dollars: float = Field(
        ..., ge=0.0, description="Current sum of market values across all positions."
    )
    post_gross_exposure_dollars: float = Field(
        ..., ge=0.0, description="Projected gross exposure dollars including proposed."
    )
    gross_exposure_pct: float = Field(
        ..., ge=0.0, description="Projected gross exposure as fraction of equity."
    )
    proposed_symbol_risk_pct: float = Field(
        ...,
        ge=0.0,
        description="Resulting aggregate risk for proposed symbol as fraction of equity.",
    )
    max_aggregate_position_risk_pct: float = Field(
        ..., gt=0.0, description="Configured aggregate risk ceiling per symbol."
    )
    proposed_single_trade_risk_pct: float = Field(
        ..., ge=0.0, description="Incremental proposed trade risk as fraction of equity."
    )
    max_risk_per_trade_pct: float = Field(
        ..., gt=0.0, description="Configured single-trade risk ceiling."
    )
    rejection_reason: str | None = Field(
        default=None, description="Primary rejection reason if unapproved, or None."
    )
    rejection_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Comprehensive list of all violated constraints."
    )
    timestamp: datetime = Field(
        ..., description="Audit timestamp echoed directly from input snapshot."
    )


class ConcentrationIntakeStatus(StrEnum):
    """Machine-readable intake gating status for concentration evaluation."""

    APPROVED = "APPROVED"
    BLOCKED_ORGANIC_BREACH = "BLOCKED_ORGANIC_BREACH"
    BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED = "BLOCKED_NEW_INTAKE_CAPACITY_EXCEEDED"
    BLOCKED_UNKNOWN_INPUT = "BLOCKED_UNKNOWN_INPUT"


class ConcentrationAssessment(BaseModel):
    """Immutable audit receipt produced by the Guardian concentration intake evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ConcentrationIntakeStatus = Field(
        ..., description="Machine-readable intake gating decision status."
    )
    is_approved: bool = Field(
        ..., description="True if proposed exposure complies with concentration limits."
    )
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    current_position_market_value: float = Field(
        ..., ge=0.0, description="Current market value of position in symbol (0.0 if new entry)."
    )
    current_concentration_pct: float = Field(
        ..., ge=0.0, description="Current position weight as fraction of total equity."
    )
    proposed_additional_market_value: float = Field(
        ..., gt=0.0, description="Notional market value of the proposed addition."
    )
    post_position_market_value: float = Field(
        ..., gt=0.0, description="Projected position market value after proposed addition."
    )
    post_concentration_pct: float = Field(
        ..., ge=0.0, description="Projected position weight as fraction of total equity."
    )
    max_position_concentration_pct: float = Field(
        ..., gt=0.0, le=1.0, description="Configured single-stock concentration cap."
    )
    is_currently_breached: bool = Field(
        ..., description="True if position has organically breached the intake concentration cap."
    )
    rejection_reason: str | None = Field(
        default=None, description="Primary human-readable rejection reason if not approved."
    )
    rejection_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Comprehensive list of all violated constraints."
    )
    timestamp: datetime = Field(
        ..., description="Audit timestamp echoed directly from input snapshot."
    )


class CapitalAllocationAssessment(BaseModel):
    """Immutable audit receipt produced by the Guardian capital separation evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapitalSleeveStatus = Field(
        ..., description="Machine-readable capital separation decision status."
    )
    is_approved: bool = Field(
        ..., description="True if proposed exposure complies with capital separation rules."
    )
    target_sleeve: CapitalSleeve = Field(
        ..., description="Target capital sleeve for the proposed exposure."
    )
    portfolio_equity: float = Field(..., gt=0.0, description="Total portfolio equity anchor.")
    core_allocation_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Configured Core capital allocation percentage (e.g. 0.80).",
    )
    core_capacity_dollars: float = Field(
        ..., ge=0.0, description="Maximum dollar capacity allocated to Core trading."
    )
    current_core_deployed_dollars: float = Field(
        ..., ge=0.0, description="Current market value deployed in Core sleeve positions."
    )
    available_core_dollars: float = Field(
        ..., description="Available headroom remaining in Core sleeve before proposed trade."
    )
    proposed_market_value: float = Field(
        ..., gt=0.0, description="Notional market value of the proposed addition."
    )
    post_core_deployed_dollars: float = Field(
        ..., ge=0.0, description="Projected Core deployed dollars after proposed addition."
    )
    reserve_allocation_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Configured Reserve capital allocation percentage (e.g. 0.15).",
    )
    reserve_isolated_dollars: float = Field(
        ..., ge=0.0, description="Total quarantined Reserve capital in dollars (INV-012)."
    )
    black_swan_allocation_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Configured Black Swan capital allocation percentage (e.g. 0.05).",
    )
    black_swan_isolated_dollars: float = Field(
        ..., ge=0.0, description="Total quarantined Black Swan capital in dollars (INV-013)."
    )
    rejection_reason: str | None = Field(
        default=None, description="Primary human-readable rejection reason if not approved."
    )
    rejection_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Comprehensive list of all violated constraints."
    )
    timestamp: datetime = Field(
        ..., description="Audit timestamp echoed directly from input snapshot."
    )


class GateDomain(StrEnum):
    """The three mandatory verification domains of the Execution Gate (FR-012)."""

    SYSTEM_FEED = "SYSTEM_FEED"
    INTAKE_RISK = "INTAKE_RISK"
    EXECUTION_SAFETY = "EXECUTION_SAFETY"


class GateDecisionStatus(StrEnum):
    """Deterministic decision status emitted by the Execution Gate."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HELD = "HELD"


class SystemFeedContext(BaseModel):
    """Contextual system health and market feed inputs for Gate domain 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_system_operational: bool = Field(
        default=True, description="Whether core system operations are healthy."
    )
    is_broker_connected: bool = Field(
        default=True, description="Whether broker connectivity is active."
    )
    is_emergency_stop_active: bool = Field(
        default=False, description="Whether emergency stop/halt circuit is active."
    )
    feed_timestamp: datetime = Field(
        ..., description="Timestamp of the most recent market data quote/bar."
    )
    evaluation_timestamp: datetime = Field(
        ..., description="Authoritative timestamp when Gate evaluation occurs."
    )
    max_feed_staleness_seconds: float = Field(
        default=60.0, gt=0.0, description="Maximum allowable age of market feed before stale."
    )


class ExecutionSafetyContext(BaseModel):
    """Safety and inflight order constraints for Gate domain 3."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active_in_flight_orders: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Symbols with orders currently in-flight at the broker.",
    )
    min_order_shares: int = Field(
        default=1, gt=0, description="Minimum order share size constraint."
    )
    max_order_shares: int = Field(
        default=1_000_000, gt=0, description="Maximum single-order share constraint."
    )


class GateCheckResult(BaseModel):
    """Discrete check result within an Execution Gate domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: GateDomain = Field(..., description="Functional domain of this check.")
    check_name: str = Field(..., min_length=1, description="Unique check rule identifier.")
    passed: bool = Field(..., description="Whether this check passed.")
    detail: str | None = Field(default=None, description="Diagnostic detail or failure message.")


class GateExecutionReceipt(BaseModel):
    """Immutable audit receipt issued by the Execution Gate (sole execution authority)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_decision_id: str = Field(
        ..., min_length=1, description="Deterministic decision receipt ID."
    )
    order_id: str = Field(..., min_length=1, description="Target order identifier.")
    symbol: str = Field(..., min_length=1, description="Target ticker symbol.")
    status: GateDecisionStatus = Field(..., description="Overall gate decision status.")
    is_approved: bool = Field(..., description="True if and only if all three domains passed.")
    portfolio_risk_assessment: PortfolioRiskAssessment | None = Field(
        default=None, description="Underlying PortfolioRiskAssessment receipt if evaluated."
    )
    concentration_assessment: ConcentrationAssessment | None = Field(
        default=None, description="Underlying ConcentrationAssessment receipt if evaluated."
    )
    capital_allocation_assessment: CapitalAllocationAssessment | None = Field(
        default=None, description="Underlying CapitalAllocationAssessment receipt if evaluated."
    )
    check_results: tuple[GateCheckResult, ...] = Field(
        default_factory=tuple, description="Granular record of all checks evaluated."
    )
    rejection_reason: str | None = Field(
        default=None, description="Primary human-readable rejection rationale."
    )
    rejection_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Comprehensive list of all violated checks."
    )
    timestamp: datetime = Field(
        ..., description="Deterministic clock anchor echoed from evaluation_timestamp."
    )


class ProtectionStatus(StrEnum):
    """Lifecycle and verification states of a position's protective stop order."""

    VERIFIED = "VERIFIED"
    UNPROTECTED = "UNPROTECTED"
    AMBIGUOUS = "AMBIGUOUS"
    RECOVERING = "RECOVERING"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    LIQUIDATING = "LIQUIDATING"


class ProtectionAction(StrEnum):
    """Deterministic actions required under the Protection Contract (FR-014)."""

    MAINTAIN_PROTECTION = "MAINTAIN_PROTECTION"
    BLOCK_ADDITIONAL_EXPOSURE = "BLOCK_ADDITIONAL_EXPOSURE"
    ATTEMPT_RECOVERY = "ATTEMPT_RECOVERY"
    TRIGGER_SAFETY_LIQUIDATION = "TRIGGER_SAFETY_LIQUIDATION"


class ProtectionContract(BaseModel):
    """Authoritative protection state binding an open position to its broker stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1, description="Associated position identifier.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    shares: int = Field(..., gt=0, description="Position share quantity requiring protection.")
    status: ProtectionStatus = Field(..., description="Active protection lifecycle status.")
    active_stop_order_id: str | None = Field(
        default=None, description="Broker stop order ID if active."
    )
    active_stop_price: float | None = Field(
        default=None, description="Active broker stop price if verified."
    )
    invalidation_price: float = Field(
        ..., gt=0.0, description="Deterministic strategy invalidation stop threshold."
    )
    recovery_attempts: int = Field(
        default=0, ge=0, description="Count of recovery attempts performed."
    )
    max_recovery_attempts: int = Field(
        default=2, gt=0, description="Maximum recovery attempts before liquidation."
    )
    is_verified: bool = Field(
        ..., description="True if and only if protection is currently confirmed active."
    )
    last_verified_at: datetime = Field(
        ..., description="Timestamp of the most recent verification receipt."
    )


class ProtectionDecision(BaseModel):
    """Immutable audit receipt issued by the protection evaluation engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(..., min_length=1, description="Unique decision receipt ID.")
    position_id: str = Field(..., min_length=1, description="Associated position ID.")
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    current_status: ProtectionStatus = Field(..., description="Protection status at evaluation.")
    action: ProtectionAction = Field(..., description="Required deterministic safety action.")
    can_add_exposure: bool = Field(
        ..., description="True if position is permitted to receive additional exposure."
    )
    reason: str = Field(..., min_length=1, description="Deterministic rationale for action.")
    timestamp: datetime = Field(..., description="Clock anchor of the decision.")


class EmergencyStopMode(StrEnum):
    """Operational mode of the emergency stop subsystem (FR-015)."""

    NORMAL = "NORMAL"
    PAUSED = "PAUSED"
    EMERGENCY_HALT = "EMERGENCY_HALT"
    LIQUIDATING = "LIQUIDATING"
    HALTED = "HALTED"


class EmergencyTriggerType(StrEnum):
    """Source classification of an emergency stop trigger."""

    MANUAL_OPERATOR = "MANUAL_OPERATOR"
    PORTFOLIO_DRAWDOWN_BREACH = "PORTFOLIO_DRAWDOWN_BREACH"
    SYSTEM_FEED_OUTAGE = "SYSTEM_FEED_OUTAGE"
    PROTECTION_FAILURE = "PROTECTION_FAILURE"
    CORRUPT_INPUT_FAIL_CLOSED = "CORRUPT_INPUT_FAIL_CLOSED"


class LiquidationScope(StrEnum):
    """Scope of positions targeted for safety liquidation."""

    CORE_ONLY = "CORE_ONLY"
    ALL_POSITIONS = "ALL_POSITIONS"
    SPECIFIC_SYMBOLS = "SPECIFIC_SYMBOLS"


class EmergencyLiquidationOrder(BaseModel):
    """Deterministic liquidation order generated during emergency stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(
        ..., min_length=1, description="Deterministic unique liquidation order ID."
    )
    symbol: str = Field(..., min_length=1, description="Ticker symbol.")
    shares: int = Field(..., gt=0, description="Exact share quantity to liquidate.")
    side: str = Field(
        default="SELL", description="Order side (strictly SELL for long liquidation)."
    )
    order_type: str = Field(
        default="MARKET", description="Order type (MARKET for urgent emergency fill)."
    )
    target_sleeve: CapitalSleeve = Field(
        default=CapitalSleeve.CORE, description="Capital sleeve of position being liquidated."
    )
    reason: str = Field(..., min_length=1, description="Deterministic reason for liquidation.")
    created_at: datetime = Field(..., description="Deterministic clock anchor.")


class EmergencyActionReceipt(BaseModel):
    """Immutable audit receipt issued by the emergency evaluation engine (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic unique receipt ID.")
    timestamp: datetime = Field(..., description="Clock anchor echoed from evaluation timestamp.")
    previous_mode: EmergencyStopMode = Field(..., description="Previous emergency stop mode.")
    current_mode: EmergencyStopMode = Field(
        ..., description="Evaluated current emergency stop mode."
    )
    trigger_type: EmergencyTriggerType | None = Field(
        default=None, description="Primary trigger category if escalated."
    )
    trigger_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Deterministic diagnostic reasons for emergency state."
    )
    orders_to_cancel: tuple[str, ...] = Field(
        default_factory=tuple, description="IDs of pending orders to cancel immediately."
    )
    liquidation_orders: tuple[EmergencyLiquidationOrder, ...] = Field(
        default_factory=tuple, description="Deterministic safety liquidation orders generated."
    )
    is_gate_locked: bool = Field(
        ..., description="True if Execution Gate is locked against new intake orders."
    )
    is_liquidation_required: bool = Field(
        ..., description="True if emergency safety liquidation must be executed."
    )
