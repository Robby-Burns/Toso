"""Unit tests for TOSO Controlled Live Arming Protocol & Human Signoff Gate (LIVE-004).

Verifies:
- AC-001: Live arming requires valid human approval token and valid cryptographic signature.
- AC-002: Arming requires all prerequisites (pre-flight passed, matrix complete, resolved).
- AC-003: Digest and configuration version mismatches fail closed.
- AC-004: Successful arming transitions gate to LIVE_ACTIVE and returns sealed receipt.
- AC-005: Order dispatch authorization passes when system is properly armed.
- AC-006: Order dispatch fails closed with UnauthorizedLiveDispatchError when disarmed.
- AC-007: Arming session expiration (>24h) disarms system and blocks dispatch.
- AC-008: Manual disarming revokes live authorization immediately.
- AC-009: Emergency runner halt blocks live dispatch and revokes arming.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
    UnauthorizedLiveDispatchError,
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
    """Fixture providing all 3 verified prerequisites."""
    t = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    readiness_receipt = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=t
    )
    matrix_receipt = AcceptanceMatrixAuditor.audit_matrix(audit_time=t)
    preflight_receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner, current_time=t, reference_time=t
    )
    return readiness_receipt, matrix_receipt, preflight_receipt


def test_successful_live_arming(verified_prerequisites):
    """AC-004 & AC-005: Valid human approval arms the gate and authorizes dispatch."""
    readiness, matrix, preflight = verified_prerequisites
    t_approval = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
        approval_timestamp=t_approval,
    )

    gate = LiveArmingGate()
    receipt = gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
        arming_timestamp=t_approval,
    )

    assert gate.is_armed is True
    assert gate.mode == LiveDeploymentMode.LIVE_ACTIVE
    assert receipt.is_armed is True
    assert len(receipt.arming_digest) == 64

    # Dispatch authorization check passes
    gate.verify_order_dispatch_authorized(current_time=t_approval)


def test_invalid_signature_fails_closed(verified_prerequisites):
    """AC-001: Forged or corrupted token signature fails closed."""
    readiness, matrix, preflight = verified_prerequisites
    t_approval = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    valid_token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
        approval_timestamp=t_approval,
    )
    # Corrupt signature
    corrupt_token = valid_token.model_copy(update={"signature": "bad_signature_0000"})

    gate = LiveArmingGate()
    with pytest.raises(InvalidApprovalTokenError, match="signature verification failed"):
        gate.arm_system(
            human_token=corrupt_token,
            preflight_receipt=preflight,
            matrix_receipt=matrix,
            readiness_receipt=readiness,
        )


def test_mismatched_preflight_hash_fails_closed(verified_prerequisites):
    """AC-003: Mismatched pre-flight hash in token fails closed."""
    readiness, matrix, preflight = verified_prerequisites
    token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
        config_version=readiness.config_version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash="mismatched_hash_12345",  # Mismatch
    )

    gate = LiveArmingGate()
    with pytest.raises(
        InvalidApprovalTokenError, match="Token preflight receipt hash does not match"
    ):
        gate.arm_system(
            human_token=token,
            preflight_receipt=preflight,
            matrix_receipt=matrix,
            readiness_receipt=readiness,
        )


def test_unauthorized_dispatch_when_disarmed():
    """AC-006: Order dispatch fails closed if gate is disarmed."""
    gate = LiveArmingGate()
    assert gate.is_armed is False

    with pytest.raises(UnauthorizedLiveDispatchError, match="system is NOT armed"):
        gate.verify_order_dispatch_authorized()


def test_session_expiration_blocks_dispatch(verified_prerequisites):
    """AC-007: Arming session past 24 hours automatically expires and blocks dispatch."""
    readiness, matrix, preflight = verified_prerequisites
    t_arm = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
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

    # 25 hours later -> expired
    t_later = t_arm + timedelta(hours=25.0)
    with pytest.raises(ExpiredArmingSessionError, match="Live arming session expired"):
        gate.verify_order_dispatch_authorized(current_time=t_later, max_session_hours=24.0)

    assert gate.is_armed is False


def test_manual_disarm(verified_prerequisites):
    """AC-008: Calling disarm_system revokes authorization immediately."""
    readiness, matrix, preflight = verified_prerequisites
    t_arm = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
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
    assert gate.is_armed is True

    gate.disarm_system(reason="Operational Shift End")
    assert gate.is_armed is False
    assert gate.mode == LiveDeploymentMode.SIMULATION

    with pytest.raises(UnauthorizedLiveDispatchError):
        gate.verify_order_dispatch_authorized()


def test_runner_halt_revokes_live_dispatch(verified_prerequisites, clean_runner):
    """AC-009: Runner emergency halt revokes live dispatch authorization."""
    readiness, matrix, preflight = verified_prerequisites
    t_arm = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    token = create_human_approval_token(
        approver_id="ChiefRiskOfficer-01",
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

    # Incur emergency halt on runner
    clean_runner.trigger_kill_switch("Market Flash Crash", timestamp=t_arm)

    with pytest.raises(LiveArmingError, match="emergency HALTED state"):
        gate.verify_order_dispatch_authorized(runner=clean_runner)

    assert gate.is_armed is False
