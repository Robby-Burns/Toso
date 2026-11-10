"""TOSO Scientist Domain Models.

Immutable data structures for append-only trade journaling, cryptographic chain verification,
and research observations (FR-016, FR-017, INV-001, INV-006).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JournalEventType(StrEnum):
    """Authoritative event taxonomy required for complete trading reconstruction (FR-016)."""

    CONFIGURATION_LOADED = "CONFIGURATION_LOADED"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIZING_CALCULATED = "SIZING_CALCULATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    EXECUTION_SUBMITTED = "EXECUTION_SUBMITTED"
    BROKER_FILL = "BROKER_FILL"
    POSITION_CHANGED = "POSITION_CHANGED"
    INVALIDATION_TRIGGERED = "INVALIDATION_TRIGGERED"
    PYRAMID_ADDED = "PYRAMID_ADDED"
    DEFENSE_TRIGGERED = "DEFENSE_TRIGGERED"
    FAILURE_RECORDED = "FAILURE_RECORDED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    OBSERVATION = "OBSERVATION"


class JournalEntry(BaseModel):
    """Immutable, cryptographically chained journal entry supporting auditability (FR-016)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(..., min_length=1, description="Unique deterministic entry identifier.")
    sequence_number: int = Field(
        ..., ge=0, description="Strictly monotonically increasing zero-based sequence."
    )
    timestamp: datetime = Field(..., description="Timestamp anchor echoed at recording time.")
    event_type: JournalEventType = Field(..., description="Categorical trading event.")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Structured event metadata and state snapshot."
    )
    previous_entry_hash: str = Field(
        ..., min_length=1, description="SHA-256 hash of preceding journal entry ('GENESIS' for 0)."
    )
    entry_hash: str = Field(
        ..., min_length=1, description="SHA-256 hash sealing this entry and preceding link."
    )


class ObservationRecord(BaseModel):
    """Structured research observation recorded by the Scientist layer (FR-017)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(..., min_length=1, description="Unique observation ID.")
    timestamp: datetime = Field(..., description="Timestamp when observation was captured.")
    topic: str = Field(..., min_length=1, description="Research topic or anomaly category.")
    symbol: str | None = Field(default=None, description="Associated asset ticker if applicable.")
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Observed quantitative research metrics."
    )
    notes: str = Field(default="", description="Descriptive notes and analytical observations.")


class JournalVerificationReceipt(BaseModel):
    """Immutable receipt confirming cryptographic integrity of the journal chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique verification receipt ID.")
    is_valid: bool = Field(..., description="True if sequence and cryptographic hashes are intact.")
    total_entries: int = Field(..., ge=0, description="Total count of entries verified.")
    latest_sequence_number: int = Field(
        ..., ge=-1, description="Highest sequence number verified (-1 if empty)."
    )
    latest_entry_hash: str = Field(..., description="Hash of the latest verified entry in chain.")
    verification_timestamp: datetime = Field(..., description="Timestamp of verification.")
    details: str = Field(default="", description="Diagnostic details or error report.")


class ScientistRole(StrEnum):
    """Permitted roles within the Scientist layer, none of which possess execution authority."""

    RESEARCHER = "RESEARCHER"
    OBSERVER = "OBSERVER"
    ANALYST = "ANALYST"


class HumanApprovalToken(BaseModel):
    """Cryptographic authorization token issued by human owner for production changes (INV-014)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_id: str = Field(..., min_length=1, description="Deterministic token identifier.")
    approver_id: str = Field(..., min_length=1, description="Authorized human owner identifier.")
    target_proposal_hash: str = Field(
        ..., min_length=1, description="SHA-256 hash of the proposal being approved."
    )
    signature: str = Field(..., min_length=1, description="Digital signature/seal.")
    approved_at: datetime = Field(..., description="Timestamp when human granted approval.")
    expires_at: datetime = Field(..., description="Expiration timestamp for token validity.")


class ProposalStatus(StrEnum):
    """Lifecycle status for production change proposals (FR-019, INV-014, INV-015)."""

    DRAFT = "DRAFT"
    SUBMITTED_FOR_REVIEW = "SUBMITTED_FOR_REVIEW"
    APPROVED = "APPROVED"
    ADOPTED = "ADOPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ProductionChangeProposal(BaseModel):
    """Scientist-generated research proposal for a production change (INV-014)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(..., min_length=1, description="Unique proposal identifier.")
    hypothesis_id: str = Field(..., min_length=1, description="Associated research hypothesis ID.")
    proposed_parameters: dict[str, Any] = Field(
        ..., description="Proposed production parameter modifications."
    )
    rationale: str = Field(..., min_length=1, description="Evidence-backed research rationale.")
    proposal_hash: str = Field(
        ..., min_length=1, description="SHA-256 seal of proposed parameters and rationale."
    )
    created_at: datetime = Field(..., description="Timestamp when proposal was formulated.")
    status: ProposalStatus = Field(
        default=ProposalStatus.DRAFT, description="Proposal lifecycle state."
    )
    is_approved: bool = Field(
        default=False, description="True only if valid human approval token is attached."
    )
    approval_token: HumanApprovalToken | None = Field(
        default=None, description="Cryptographic human approval token if approved."
    )
    reviewed_at: datetime | None = Field(
        default=None, description="Timestamp of human approval or rejection."
    )
    adopted_at: datetime | None = Field(
        default=None, description="Timestamp when adopted into production config."
    )
    rejection_reason: str | None = Field(
        default=None, description="Audit reason for proposal rejection."
    )


class ProposalReceipt(BaseModel):
    """Immutable audit receipt issued upon proposal registration or state change (INV-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique deterministic receipt ID.")
    proposal_id: str = Field(..., min_length=1, description="Target proposal identifier.")
    action: str = Field(..., min_length=1, description="Action performed, e.g. 'REGISTERED'.")
    previous_status: ProposalStatus | None = Field(
        default=None, description="Previous status before action."
    )
    new_status: ProposalStatus = Field(..., description="Current status after action.")
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
    checksum: str = Field(..., min_length=1, description="SHA-256 seal of proposal data.")
    details: str = Field(default="", description="Audit details and runtime notes.")


class ExecutionInterceptionReceipt(BaseModel):
    """Audit receipt generated when execution attempts from Scientist are blocked (INV-014)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique receipt identifier.")
    interception_timestamp: datetime = Field(..., description="Timestamp of blocked attempt.")
    action_attempted: str = Field(..., min_length=1, description="Attempted execution action.")
    caller_subsystem: str = Field(
        ..., min_length=1, description="Originating subsystem (e.g. 'Scientist')."
    )
    is_blocked: bool = Field(default=True, description="Always True; execution is never allowed.")
    security_reason: str = Field(..., min_length=1, description="Security policy rationale.")


class HypothesisStatus(StrEnum):
    """Lifecycle status for a scientific research hypothesis."""

    DRAFT = "DRAFT"
    TESTING = "TESTING"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    ABANDONED = "ABANDONED"


class HypothesisStatusEntry(BaseModel):
    """Immutable transition audit log entry for a research hypothesis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HypothesisStatus = Field(..., description="Target status transitioned to.")
    timestamp: datetime = Field(..., description="Timestamp of status transition.")
    reason: str = Field(default="", description="Audit rationale for transition.")


class ResearchHypothesis(BaseModel):
    """Immutable, falsifiable scientific research hypothesis (FR-017)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: str = Field(..., min_length=1, description="Deterministic unique identifier.")
    title: str = Field(..., min_length=1, description="Short descriptive title.")
    statement: str = Field(..., min_length=1, description="Falsifiable scientific statement.")
    rationale: str = Field(..., min_length=1, description="Analytical grounding/observation.")
    target_parameters: tuple[str, ...] = Field(
        default_factory=tuple, description="Strategy parameters under investigation."
    )
    expected_direction: str = Field(
        ..., min_length=1, description="Predicted performance effect (e.g. higher Sharpe)."
    )
    status: HypothesisStatus = Field(
        default=HypothesisStatus.DRAFT, description="Current lifecycle state."
    )
    created_at: datetime = Field(..., description="Timestamp hypothesis was first formed.")
    updated_at: datetime = Field(..., description="Timestamp of most recent state update.")
    status_history: tuple[HypothesisStatusEntry, ...] = Field(
        default_factory=tuple, description="Immutable state transition audit trail."
    )
    evidence_summary: str | None = Field(
        default=None, description="Summary of experiment and backtest evidence."
    )


class HypothesisReceipt(BaseModel):
    """Audit receipt issued upon hypothesis creation or status transition (INV-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique receipt identifier.")
    hypothesis_id: str = Field(..., min_length=1, description="Target hypothesis identifier.")
    action: str = Field(..., min_length=1, description="Action performed (CREATED/TRANSITIONED).")
    previous_status: HypothesisStatus | None = Field(
        default=None, description="Previous status prior to action."
    )
    new_status: HypothesisStatus = Field(..., description="Current status after action.")
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
    details: str = Field(default="", description="Audit diagnostic notes.")


class ExperimentStatus(StrEnum):
    """Lifecycle status for a scientific backtest / research experiment."""

    DRAFT = "DRAFT"
    CONFIGURED = "CONFIGURED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DatasetSplit(BaseModel):
    """Strict temporal partitioning between discovery (in-sample) and validation (FR-018)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: datetime = Field(..., description="Beginning of the dataset interval.")
    in_sample_end_date: datetime = Field(
        ..., description="End of development/discovery period (exclusive boundary)."
    )
    out_of_sample_end_date: datetime = Field(
        ..., description="End of held-out validation period (strictly > in_sample_end_date)."
    )
    symbols: tuple[str, ...] = Field(
        ..., min_length=1, description="Eligible asset symbols included in the experiment."
    )


class ParameterVariation(BaseModel):
    """Explicit contrast between baseline and proposed candidate parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter_name: str = Field(..., min_length=1, description="Target parameter name.")
    baseline_value: Any = Field(..., description="Authoritative baseline value.")
    candidate_value: Any = Field(..., description="Experimental candidate value under test.")
    rationale: str = Field(default="", description="Reason for testing this variation.")


class ExperimentDesign(BaseModel):
    """Immutable, reproducible scientific experiment specification (FR-017, FR-018)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(..., min_length=1, description="Unique experiment identifier.")
    hypothesis_id: str = Field(
        ..., min_length=1, description="Associated hypothesis identifier under test."
    )
    title: str = Field(..., min_length=1, description="Descriptive experiment title.")
    description: str = Field(..., min_length=1, description="Detailed methodological plan.")
    baseline_config_version: str = Field(
        ..., min_length=1, description="Reference baseline configuration version (e.g. 1.0.0)."
    )
    parameter_variations: tuple[ParameterVariation, ...] = Field(
        ..., min_length=1, description="Parameters varied against the baseline."
    )
    dataset_split: DatasetSplit = Field(
        ..., description="Strict train/validation split definition."
    )
    target_metrics: tuple[str, ...] = Field(
        ..., min_length=1, description="Quantitative performance metrics evaluated."
    )
    status: ExperimentStatus = Field(
        default=ExperimentStatus.DRAFT, description="Current experiment status."
    )
    created_at: datetime = Field(..., description="Experiment formulation timestamp.")
    updated_at: datetime = Field(..., description="Most recent update timestamp.")
    notes: str = Field(default="", description="Supplementary notes and audit observations.")


class ExperimentReceipt(BaseModel):
    """Immutable receipt issued upon experiment registration or state change (INV-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique receipt identifier.")
    experiment_id: str = Field(..., min_length=1, description="Target experiment identifier.")
    hypothesis_id: str = Field(..., min_length=1, description="Associated hypothesis ID.")
    action: str = Field(..., min_length=1, description="Action performed (REGISTERED/UPDATED).")
    previous_status: ExperimentStatus | None = Field(
        default=None, description="Previous status before action."
    )
    new_status: ExperimentStatus = Field(..., description="Current status after action.")
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
    details: str = Field(default="", description="Diagnostic details or error notes.")


class BacktestTrade(BaseModel):
    """Immutable record of an individual simulated trade executed during a backtest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_id: str = Field(..., min_length=1, description="Unique simulated trade identifier.")
    symbol: str = Field(..., min_length=1, description="Instrument ticker symbol.")
    entry_timestamp: datetime = Field(..., description="Timestamp of simulated entry fill.")
    entry_price: float = Field(..., gt=0.0, description="Simulated entry price.")
    exit_timestamp: datetime = Field(..., description="Timestamp of simulated exit fill.")
    exit_price: float = Field(..., gt=0.0, description="Simulated exit price.")
    shares: float = Field(..., gt=0.0, description="Allocated trade size in units/shares.")
    pnl: float = Field(..., description="Realized dollar profit or loss.")
    return_pct: float = Field(..., description="Percentage return on trade.")
    exit_reason: str = Field(..., min_length=1, description="Deterministic exit rationale.")


class PerformanceMetrics(BaseModel):
    """Comprehensive, deterministic trading and risk performance metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_trades: int = Field(..., ge=0, description="Total completed round-trip trades.")
    winning_trades: int = Field(..., ge=0, description="Number of profitable trades.")
    losing_trades: int = Field(..., ge=0, description="Number of unprofitable trades.")
    win_rate: float = Field(..., ge=0.0, le=1.0, description="Fraction of winning trades.")
    profit_factor: float = Field(..., ge=0.0, description="Gross profit divided by gross loss.")
    sharpe_ratio: float = Field(..., description="Annualized Sharpe ratio (0.0 if zero variance).")
    max_drawdown: float = Field(
        ..., ge=0.0, le=1.0, description="Maximum peak-to-trough decline as fraction."
    )
    max_drawdown_dollars: float = Field(
        ..., ge=0.0, description="Maximum peak-to-trough dollar decline."
    )
    total_pnl: float = Field(..., description="Cumulative net realized and mark-to-market PnL.")
    net_return_pct: float = Field(..., description="Cumulative net portfolio return percentage.")


class BacktestResult(BaseModel):
    """Immutable result and execution audit trail of a simulated historical backtest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backtest_id: str = Field(
        ..., min_length=1, description="Deterministic backtest run identifier."
    )
    experiment_id: str = Field(
        ..., min_length=1, description="Linked experiment design identifier."
    )
    config_version: str = Field(..., min_length=1, description="Baseline configuration version.")
    parameters: dict[str, Any] = Field(..., description="Actual strategy parameters evaluated.")
    metrics: PerformanceMetrics = Field(..., description="Calculated performance metrics.")
    trades: tuple[BacktestTrade, ...] = Field(
        default_factory=tuple, description="Chronological simulated trade records."
    )
    equity_curve: tuple[float, ...] = Field(
        default_factory=tuple, description="Simulated equity points at each bar."
    )
    start_date: datetime = Field(..., description="Start of historical backtest window.")
    end_date: datetime = Field(..., description="End of historical backtest window.")
    executed_at: datetime = Field(..., description="Evaluation timestamp anchor echoed.")


class BacktestComparison(BaseModel):
    """Comparative analysis contrasting baseline strategy against candidate variations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(..., min_length=1, description="Experiment design identifier.")
    hypothesis_id: str = Field(..., min_length=1, description="Linked falsifiable hypothesis ID.")
    baseline_result: BacktestResult = Field(
        ..., description="Performance under baseline parameters."
    )
    candidate_result: BacktestResult = Field(
        ..., description="Performance under experimental variation."
    )
    metric_deltas: dict[str, float] = Field(
        ..., description="Candidate minus baseline differences for all metrics."
    )
    target_metrics_improved: dict[str, bool] = Field(
        ..., description="Boolean map indicating if each target metric improved."
    )
    hypothesis_supported: bool = Field(
        ..., description="True if target metrics confirm hypothesis expected direction."
    )
    comparison_summary: str = Field(..., min_length=1, description="Descriptive audit summary.")


class BacktestReceipt(BaseModel):
    """Immutable cryptographic audit receipt issued upon backtest completion (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Unique deterministic receipt ID.")
    experiment_id: str = Field(
        ..., min_length=1, description="Linked experiment design identifier."
    )
    backtest_id: str = Field(..., min_length=1, description="Linked backtest execution ID.")
    action: str = Field(
        ..., min_length=1, description="Action performed, e.g. 'SIMULATION_COMPLETED'."
    )
    timestamp: datetime = Field(..., description="Echoed evaluation timestamp anchor.")
    checksum: str = Field(..., min_length=1, description="SHA-256 seal of backtest result data.")
    details: str = Field(default="", description="Audit details and runtime notes.")


class ValidationVerdict(StrEnum):
    """Categorical classification of out-of-sample held-out validation results (FR-018)."""

    VALIDATED = "VALIDATED"
    OVERFIT = "OVERFIT"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class DegradationAnalysis(BaseModel):
    """Mathematical evaluation of performance retention and drawdown expansion OOS vs IS."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sharpe_retention_ratio: float = Field(
        ..., description="Ratio of OOS Sharpe to IS Sharpe (e.g. 0.85 = 85% retention)."
    )
    profit_factor_retention_ratio: float = Field(
        ..., description="Ratio of OOS profit factor to IS profit factor."
    )
    drawdown_expansion_ratio: float = Field(
        ..., description="Ratio of OOS max drawdown to IS max drawdown (>1.0 indicates expansion)."
    )
    net_return_retention_ratio: float = Field(
        ..., description="Ratio of OOS return percentage to IS return percentage."
    )
    verdict: ValidationVerdict = Field(..., description="Classification outcome.")
    notes: str = Field(default="", description="Audit observations and rationale.")


class OOSEvaluationResult(BaseModel):
    """Immutable record of out-of-sample validation against held-out data (FR-018)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str = Field(..., min_length=1, description="Deterministic evaluation ID.")
    experiment_id: str = Field(..., min_length=1, description="Linked experiment identifier.")
    hypothesis_id: str = Field(..., min_length=1, description="Linked hypothesis identifier.")
    in_sample_result: BacktestResult = Field(..., description="In-sample discovery run results.")
    out_of_sample_result: BacktestResult = Field(
        ..., description="Out-of-sample held-out validation run results."
    )
    degradation: DegradationAnalysis = Field(..., description="Comparative degradation analysis.")
    verdict: ValidationVerdict = Field(..., description="Final validation verdict.")
    is_approved_for_proposal: bool = Field(
        ..., description="True only if verdict is VALIDATED, permitting production proposal."
    )
    evaluated_at: datetime = Field(..., description="Evaluation timestamp anchor echoed.")


class OOSEvaluationReceipt(BaseModel):
    """Immutable cryptographic audit receipt for out-of-sample evaluation (INV-001, INV-006)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., min_length=1, description="Deterministic receipt ID.")
    evaluation_id: str = Field(..., min_length=1, description="Linked evaluation identifier.")
    experiment_id: str = Field(..., min_length=1, description="Linked experiment identifier.")
    verdict: ValidationVerdict = Field(..., description="Validation outcome verdict.")
    timestamp: datetime = Field(..., description="Evaluation timestamp anchor echoed.")
    checksum: str = Field(..., min_length=1, description="SHA-256 seal of evaluation data.")
    details: str = Field(default="", description="Diagnostic details or notes.")
