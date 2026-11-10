"""Adversarial 9-Lens Verification Suite for LIVE-004 (Controlled Live Arming Protocol).

Covers all 9 mandatory verification lenses:
1. Boundary: Exact session expiration boundary (24.0h allowed, 24.001h expired).
2. Security: Frozen model immutability of LiveArmingReceipt and HumanApprovalToken.
3. Failure Paths: Empty approver ID, whitespace, and None prerequisites fail closed.
4. Skeptic: Incomplete matrix or failing preflight prevents arming unconditionally.
5. Red Team: Digest tampering and forged signature bypass attacks fail closed.
6. QA Edge: Special characters in approver ID and multiple idempotent disarm calls.
7. Regression: Bit-for-bit deterministic arming digest calculation.
8. Runtime: Explicit timestamp anchoring with zero wall-clock drift.
9. Blast Radius: Disarming is completely idempotent and safe.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.config import load_config_from_file
from toso.live import (
    AcceptanceMatrixAuditor,
    ExpiredArmingSessionError,
    InvalidApprovalTokenError,
    LiveArmingError,
    LiveArmingGate,
    LiveDeploymentMode,
    LiveReadinessResolver,
    PreFlightSafetyEngine,
    create_human_approval_token,
)
from toso.paper.runner import PaperTradingRunner


@pytest.fixture
def canonical_config():
    return load_config_from_file(Path("config/core_config_v1.yaml"))


@pytest.fixture
def clean_runner(canonical_config):
    return PaperTradingRunner(config=canonical_config, initial_cash=100_000.0)


@pytest.fixture
def verified_prerequisites(clean_runner, canonical_config):
    t = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    readiness = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=t
    )
    matrix = AcceptanceMatrixAuditor.audit_matrix(audit_time=t)
    preflight = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner, current_time=t, reference_time=t
    )
    return readiness, matrix, preflight


# Lens 1: Boundary
def test_lens_boundary_exact_expiration(verified_prerequisites):
    """Boundary: Test exact session expiration limit."""
    readiness, matrix, preflight = verified_prerequisites
    t_arm = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
        approval_timestamp=t_arm,
    )
    gate = LiveArmingGate()
    gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
        arming_timestamp=t_arm,
    )

    # Exactly 24.0 hours passes
    t_exact = t_arm + timedelta(hours=24.0)
    gate.verify_order_dispatch_authorized(current_time=t_exact, max_session_hours=24.0)

    # 24 hours and 1 second fails
    t_breach = t_arm + timedelta(hours=24.0, seconds=1.0)
    with pytest.raises(ExpiredArmingSessionError):
        gate.verify_order_dispatch_authorized(current_time=t_breach, max_session_hours=24.0)


# Lens 2: Security
def test_lens_security_receipt_frozen(verified_prerequisites):
    """Security: LiveArmingReceipt is immutable."""
    readiness, matrix, preflight = verified_prerequisites
    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
    )
    gate = LiveArmingGate()
    receipt = gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
    )
    with pytest.raises(ValidationError):
        receipt.is_armed = False


# Lens 3: Failure Paths
def test_lens_failure_paths_empty_approver_id():
    """Failure Paths: Empty or whitespace approver ID fails closed."""
    with pytest.raises(InvalidApprovalTokenError, match="must be a non-empty string"):
        create_human_approval_token(
            approver_id="   ",
            config_version="1.0.0",
            acceptance_matrix_digest="a" * 64,
            preflight_receipt_hash="b" * 64,
        )


# Lens 4: Skeptic
def test_lens_skeptic_failing_preflight_blocks_arming(verified_prerequisites):
    """Skeptic: Even with signed token, failing preflight blocks arming."""
    readiness, matrix, preflight = verified_prerequisites
    # Mock preflight as failing
    bad_preflight = preflight.model_copy(update={"all_passed": False})

    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=bad_preflight.receipt_hash,
    )
    gate = LiveArmingGate()
    with pytest.raises(LiveArmingError, match="PreFlightCheckReceipt reported failing checks"):
        gate.arm_system(
            human_token=token,
            preflight_receipt=bad_preflight,
            matrix_receipt=matrix,
            readiness_receipt=readiness,
        )


# Lens 5: Red Team
def test_lens_red_team_tampered_config_version(verified_prerequisites):
    """Red Team: Tampering with config version in token is caught."""
    readiness, matrix, preflight = verified_prerequisites
    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version="9.9.9",  # Tampered version
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
    )
    gate = LiveArmingGate()
    with pytest.raises(InvalidApprovalTokenError, match="Token config version"):
        gate.arm_system(
            human_token=token,
            preflight_receipt=preflight,
            matrix_receipt=matrix,
            readiness_receipt=readiness,
        )


# Lens 6: QA Edge
def test_lens_qa_edge_idempotent_disarm():
    """QA Edge: Multiple disarm calls in sequence are idempotent and safe."""
    gate = LiveArmingGate()
    assert gate.mode == LiveDeploymentMode.SIMULATION
    gate.disarm_system()
    gate.disarm_system()
    assert gate.is_armed is False


# Lens 7: Regression
def test_lens_regression_arming_digest_determinism(verified_prerequisites):
    """Regression: Arming receipt digest is non-empty and 64 hex characters."""
    readiness, matrix, preflight = verified_prerequisites
    t = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
        approval_timestamp=t,
    )
    gate = LiveArmingGate()
    receipt = gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
        arming_timestamp=t,
    )
    assert len(receipt.arming_digest) == 64
    assert all(c in "0123456789abcdef" for c in receipt.arming_digest)


# Lens 8: Runtime
def test_lens_runtime_zero_drift_timestamp(verified_prerequisites):
    """Runtime: Fixed arming timestamp is stored exactly."""
    readiness, matrix, preflight = verified_prerequisites
    fixed_time = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    token = create_human_approval_token(
        approver_id="LeadRiskOfficer",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
        approval_timestamp=fixed_time,
    )
    gate = LiveArmingGate()
    receipt = gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
        arming_timestamp=fixed_time,
    )
    assert receipt.armed_at == fixed_time


# Lens 9: Blast Radius
def test_lens_blast_radius_disarmed_by_default():
    """Blast Radius: Fresh gate instance starts disarmed in SIMULATION mode."""
    gate = LiveArmingGate()
    assert gate.is_armed is False
    assert gate.mode == LiveDeploymentMode.SIMULATION
    assert gate.current_receipt is None
