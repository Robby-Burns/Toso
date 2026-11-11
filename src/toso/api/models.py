"""TOSO Web API Models.

Defines Pydantic models for REST endpoints and sequenced WebSocket telemetry,
adhering strictly to the decoupled 4-domain top-bar and 5-state Guardian model.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentMode(StrEnum):
    """Execution deployment environment."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionAuthorization(StrEnum):
    """Authorization status for live order dispatch."""

    ARMED = "ARMED"
    DISARMED = "DISARMED"


class BrokerLinkState(StrEnum):
    """Broker connection and heartbeat status."""

    CONNECTED = "CONNECTED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


class ReconciliationState(StrEnum):
    """Authoritative local vs broker state reconciliation status."""

    RECONCILED = "RECONCILED"
    DRIFT = "DRIFT"
    AMBIGUOUS = "AMBIGUOUS"


class GuardianCheckStatus(StrEnum):
    """5-State Guardian evaluation outcome."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class TopBarTelemetry(BaseModel):
    """Independent decoupled top-bar telemetry indicators."""

    model_config = ConfigDict(frozen=True)

    environment: EnvironmentMode = Field(..., description="Target execution environment.")
    authorization: ExecutionAuthorization = Field(..., description="Live dispatch authorization.")
    broker_link: BrokerLinkState = Field(..., description="Broker link status.")
    reconciliation: ReconciliationState = Field(..., description="State parity status.")
    broker_latency_ms: float = Field(..., description="Broker round-trip heartbeat latency.")
    last_heartbeat: datetime = Field(..., description="UTC timestamp of last valid broker tick.")


class CapitalDistribution(BaseModel):
    """TOSO approved capital allocation and deployed heat."""

    model_config = ConfigDict(frozen=True)

    total_equity: float = Field(..., description="Total portfolio net asset value.")
    core_allocated: float = Field(..., description="80% Core Trader allocation.")
    reserve_allocated: float = Field(..., description="15% Reserve cash allocation.")
    black_swan_allocated: float = Field(..., description="5% Black Swan defense allocation.")
    deployed_capital: float = Field(..., description="Capital currently engaged in open trades.")
    available_capital: float = Field(..., description="Uncommitted cash available for new intake.")
    unrealized_pnl: float = Field(..., description="Unrealized mark-to-market PnL in dollars.")
    daily_drawdown_pct: float = Field(
        ..., description="Current drawdown percentage from high-water mark."
    )


class OpenPositionItem(BaseModel):
    """Active open position with stop invalidation and R-risk exposure."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    shares: int
    entry_price: float
    current_price: float
    invalidation_stop: float
    unrealized_pnl: float
    unrealized_r: float
    pyramid_tier: int = 0


class GuardianCheckItem(BaseModel):
    """Individual Guardian safety verification check."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    domain: str = Field(..., description="SYSTEM_AND_FEED | INTAKE_AND_RISK | EXECUTION_SAFETY")
    name: str
    status: GuardianCheckStatus
    details: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GuardianSummaryResponse(BaseModel):
    """Consolidated Guardian execution gate audit response."""

    model_config = ConfigDict(frozen=True)

    overall_status: GuardianCheckStatus
    domain_statuses: dict[str, GuardianCheckStatus]
    checks: list[GuardianCheckItem]


class SystemStateResponse(BaseModel):
    """Complete consolidated system state snapshot."""

    model_config = ConfigDict(frozen=True)

    top_bar: TopBarTelemetry
    capital: CapitalDistribution
    open_positions: list[OpenPositionItem]
    total_r_exposure: float = Field(..., description="Aggregate portfolio R exposure.")
    max_r_limit: float = Field(default=6.0, description="Approved 6.0R portfolio heat limit.")
    active_config_version: str


class EmergencyDisarmRequest(BaseModel):
    """Payload for instantaneous emergency halt."""

    reason: str = Field(default="MANUAL_EMERGENCY_DISARM")


class EmergencyDisarmResponse(BaseModel):
    """Response confirming immediate disarm."""

    model_config = ConfigDict(frozen=True)

    success: bool
    status: str = "DISARMED"
    disarmed_at: datetime
    reason: str


class PreflightResponse(BaseModel):
    """Response from executing pre-flight safety checks."""

    model_config = ConfigDict(frozen=True)

    receipt_id: str
    all_passed: bool
    receipt_hash: str
    checks: list[dict[str, Any]]


class ArmingRequest(BaseModel):
    """Request payload for live arming."""

    approver_id: str = Field(..., min_length=1)
    secret_salt: str = Field(default="TOSO-LIVE-ARMING-AUTH-v1")
    notes: str = Field(default="Live arming authorized via API.")


class ArmingResponse(BaseModel):
    """Response confirming live arming verification."""

    model_config = ConfigDict(frozen=True)

    success: bool
    arming_id: str
    mode: str
    armed_at: datetime
    arming_digest: str


class WebSocketTelemetryMessage(BaseModel):
    """Sequenced broadcast message for client state synchronization."""

    model_config = ConfigDict(frozen=True)

    sequence_id: int
    timestamp: datetime
    event_type: str
    payload: dict[str, Any]


class StateSyncResponse(BaseModel):
    """Reconnection synchronization response containing current state and missed events."""

    model_config = ConfigDict(frozen=True)

    current_sequence_id: int
    state: SystemStateResponse
    missed_events: list[WebSocketTelemetryMessage]


class DefensiveInstrumentItem(BaseModel):
    """Approved Black Swan defensive instrument."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    instrument_type: str
    leverage_factor: float
    underlying_index: str
    max_allocation_of_sleeve_pct: float


class BlackSwanDefenseResponse(BaseModel):
    """Authoritative audit response verifying Black Swan ring-fencing and mutual exclusivity."""

    model_config = ConfigDict(frozen=True)

    allocated_capital: float
    allocated_pct: float
    status: str
    is_ring_fenced: bool = True
    core_symbols: list[str]
    defensive_symbols: list[DefensiveInstrumentItem]
    mutual_exclusivity_verified: bool = True
    collision_count: int = 0


class SimulationRequest(BaseModel):
    """Request payload to test fail-closed behavior under simulated edge conditions."""

    scenario: str = Field(..., description="STALE_FEED | CLOCK_SKEW | CONCENTRATION_HOLD | RESET")


class SimulationResponse(BaseModel):
    """Outcome of simulated failure injection."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    applied: bool
    guardian_summary: GuardianSummaryResponse
    top_bar: TopBarTelemetry


class BacktestMetrics(BaseModel):
    """Standard quantitative backtesting & OOS performance metrics."""

    model_config = ConfigDict(frozen=True)

    sharpe_ratio: float = Field(..., description="Annualized Sharpe ratio.")
    calmar_ratio: float = Field(..., description="Annualized Calmar ratio.")
    max_drawdown_pct: float = Field(..., description="Peak-to-trough max drawdown percentage.")
    win_rate_pct: float = Field(..., description="Win rate percentage.")
    profit_factor: float = Field(..., description="Gross profit / Gross loss ratio.")
    expectancy_r: float = Field(..., description="Mathematical trade expectancy in R.")
    total_trades: int = Field(..., description="Total trades executed in evaluation window.")


class ProposalItem(BaseModel):
    """Scientist research proposal with diff data and cryptographic seal."""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    hypothesis_id: str
    rationale: str
    proposed_parameters: dict[str, Any]
    proposal_hash: str
    status: str
    is_approved: bool
    created_at: datetime
    reviewed_at: datetime | None = None
    adopted_at: datetime | None = None
    rejection_reason: str | None = None
    approver_id: str | None = None
    baseline_metrics: BacktestMetrics | None = None
    proposed_metrics: BacktestMetrics | None = None
    equity_curve_baseline: list[float] | None = None
    equity_curve_proposed: list[float] | None = None
    yaml_diff: str | None = None


class ScientistConfigResponse(BaseModel):
    """Active CoreConfig and metadata inspected from Scientist Studio."""

    model_config = ConfigDict(frozen=True)

    raw_yaml: str
    version: str
    status: str
    approved_by: str | None
    approved_at: str | None
    checksum: str
    description: str
    strategy_params: dict[str, Any]


class CreateProposalRequest(BaseModel):
    """Request payload to formulate a new research proposal."""

    proposal_id: str
    hypothesis_id: str
    rationale: str
    proposed_parameters: dict[str, Any]


class ReviewProposalRequest(BaseModel):
    """Request payload to transition proposal lifecycle state."""

    action: str = Field(..., description="SUBMIT | REJECT | WITHDRAW")
    reason: str = ""


class ApproveProposalRequest(BaseModel):
    """Request payload to cryptographically approve a proposal (INV-014)."""

    approver_id: str
    secret_salt: str
    validity_minutes: int = 60


class AdoptProposalRequest(BaseModel):
    """Request payload to adopt an approved proposal into production (INV-015)."""

    new_version: str
    approver_id: str
    secret_salt: str


class ProposalActionResponse(BaseModel):
    """Response returned upon lifecycle transitions and atomic adoptions."""

    model_config = ConfigDict(frozen=True)

    proposal: ProposalItem
    receipt_id: str
    checksum: str
    message: str
