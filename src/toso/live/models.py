"""TOSO Live Readiness Models.

Defines audit receipts, question resolutions, and immutable verification models
for transitioning the TOSO system to live execution readiness.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuestionStatus(StrEnum):
    """Resolution status of an operational question."""

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    DEFERRED = "DEFERRED"


class QuestionResolution(BaseModel):
    """Audit record of a single open question resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(..., description="Canonical ID of the question (e.g. 'Q-01').")
    question_text: str = Field(..., description="The substantive question text.")
    is_blocking: bool = Field(..., description="Whether this question blocks live readiness.")
    status: QuestionStatus = Field(default=QuestionStatus.RESOLVED)
    parameter_key: str = Field(
        ...,
        description="Configuration path or key governing this question.",
    )
    locked_value: Any = Field(..., description="Authoritative locked value.")
    source: str = Field(..., description="Authoritative document or configuration source.")


class LiveReadinessAuditReceipt(BaseModel):
    """Authoritative audit receipt verifying resolution of all live readiness prerequisites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the live audit receipt.",
    )
    config_version: str = Field(..., description="Version of the verified CoreConfig.")
    all_blocking_resolved: bool = Field(
        ..., description="True if and only if 100% of blocking questions are RESOLVED."
    )
    resolutions: tuple[QuestionResolution, ...] = Field(
        ..., description="Individual resolution records for all evaluated questions."
    )
    core_symbols: tuple[str, ...] = Field(..., description="Approved Core trading symbols.")
    defensive_symbols: tuple[str, ...] = Field(
        ..., description="Approved Black Swan defensive symbols."
    )
    audit_hash: str = Field(
        ..., description="SHA-256 cryptographic digest of the complete resolution state."
    )


class RequirementCategory(StrEnum):
    """Categorization of specification requirements."""

    FUNCTIONAL_REQUIREMENT = "FUNCTIONAL_REQUIREMENT"
    INVARIANT = "INVARIANT"


class RequirementTrace(BaseModel):
    """Traceability link connecting specification requirement to automated verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    req_id: str = Field(..., description="Canonical ID (e.g. 'FR-001' or 'INV-001').")
    title: str = Field(..., description="Canonical requirement title.")
    category: RequirementCategory = Field(...)
    primary_story: str = Field(..., description="Story ID in which the requirement was verified.")
    test_files: tuple[str, ...] = Field(
        ..., description="Relative test file paths verifying this requirement."
    )
    status: str = Field(default="VERIFIED", description="Verification status.")


class AcceptanceMatrixAuditReceipt(BaseModel):
    """Authoritative audit receipt verifying 100% matrix completeness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the matrix audit.",
    )
    total_requirements: int = Field(..., description="Total tracked requirements count.")
    verified_count: int = Field(..., description="Count of fully verified requirements.")
    unverified_count: int = Field(..., description="Count of unverified requirements.")
    is_complete: bool = Field(
        ..., description="True if and only if verified_count == total_requirements."
    )
    trace_records: tuple[RequirementTrace, ...] = Field(
        ..., description="Complete tuple of requirement traceability records."
    )
    matrix_digest: str = Field(..., description="SHA-256 cryptographic digest of matrix traces.")


class CheckStatus(StrEnum):
    """Execution status of an individual pre-flight safety check."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PreFlightCheckItem(BaseModel):
    """Result record of an individual pre-flight safety check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str = Field(..., description="Canonical check ID (e.g. 'PFC-01').")
    name: str = Field(..., description="Descriptive check name.")
    status: CheckStatus = Field(..., description="Passed or Failed status.")
    details: str = Field(..., description="Detailed verification explanation or measurement.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when check executed.",
    )


class PreFlightCheckReceipt(BaseModel):
    """Authoritative audit receipt verifying pre-flight readiness before live arming."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(..., description="Unique receipt identifier.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the pre-flight sequence.",
    )
    overall_status: CheckStatus = Field(..., description="PASSED if and only if all checks pass.")
    checks: tuple[PreFlightCheckItem, ...] = Field(
        ..., description="Tuple of all executed pre-flight check items."
    )
    all_passed: bool = Field(..., description="True if and only if every single check PASSED.")
    receipt_hash: str = Field(..., description="SHA-256 cryptographic digest of receipt payload.")


class LiveDeploymentMode(StrEnum):
    """Execution deployment mode for the TOSO trading engine."""

    SIMULATION = "SIMULATION"
    DRY_RUN = "DRY_RUN"
    LIVE_ACTIVE = "LIVE_ACTIVE"


class HumanApprovalToken(BaseModel):
    """Cryptographically verifiable token representing non-delegable human approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approver_id: str = Field(..., min_length=1, description="Identity of approving human owner.")
    approval_timestamp: datetime = Field(..., description="UTC timestamp of the human approval.")
    config_version: str = Field(..., description="Approved CoreConfig version string.")
    acceptance_matrix_digest: str = Field(
        ..., description="SHA-256 digest of audited Acceptance Matrix."
    )
    preflight_receipt_hash: str = Field(
        ..., description="SHA-256 digest of verified PreFlightCheckReceipt."
    )
    signature: str = Field(..., description="Cryptographic SHA-256 signature sealing the token.")
    notes: str = Field(default="", description="Optional human rationale or notes.")


class LiveArmingReceipt(BaseModel):
    """Authoritative audit receipt proving authorized live arming."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arming_id: str = Field(..., description="Unique live arming authorization identifier.")
    mode: LiveDeploymentMode = Field(..., description="Active operational mode.")
    armed_at: datetime = Field(..., description="UTC timestamp when live arming was granted.")
    human_approval: HumanApprovalToken = Field(..., description="Verified human approval token.")
    preflight_receipt: PreFlightCheckReceipt = Field(
        ..., description="Verified pre-flight check receipt."
    )
    matrix_receipt: AcceptanceMatrixAuditReceipt = Field(
        ..., description="Verified acceptance matrix receipt."
    )
    readiness_receipt: LiveReadinessAuditReceipt = Field(
        ..., description="Verified open questions resolution receipt."
    )
    is_armed: bool = Field(..., description="True if system is actively armed for live execution.")
    arming_digest: str = Field(..., description="SHA-256 cryptographic digest sealing the arming.")
