"""TOSO Controlled Live Arming Protocol & Human Signoff Gate.

Implements Specification §30, §Phase 7 Exit Conditions, and Invariants INV-014/015:
- Live order dispatch is physically impossible without an explicit, cryptographically
  sealed HumanApprovalToken.
- Validates 100% matrix completion, zero blocking open questions, and all-passed preflight checks.
- Enforces session expiration windows and instantaneous fail-closed disarming.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from toso.live.errors import (
    ExpiredArmingSessionError,
    InvalidApprovalTokenError,
    LiveArmingError,
    UnauthorizedLiveDispatchError,
)
from toso.live.models import (
    AcceptanceMatrixAuditReceipt,
    HumanApprovalToken,
    LiveArmingReceipt,
    LiveDeploymentMode,
    LiveReadinessAuditReceipt,
    PreFlightCheckReceipt,
)


def compute_approval_token_signature(
    approver_id: str,
    approval_timestamp: datetime,
    config_version: str,
    acceptance_matrix_digest: str,
    preflight_receipt_hash: str,
    secret_salt: str = "TOSO-LIVE-ARMING-AUTH-v1",
) -> str:
    """Compute the deterministic SHA-256 signature for a human approval token."""
    payload = {
        "approver_id": approver_id.strip(),
        "approval_timestamp": approval_timestamp.isoformat(),
        "config_version": config_version.strip(),
        "acceptance_matrix_digest": acceptance_matrix_digest.strip(),
        "preflight_receipt_hash": preflight_receipt_hash.strip(),
        "secret_salt": secret_salt,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_human_approval_token(
    approver_id: str,
    config_version: str,
    acceptance_matrix_digest: str,
    preflight_receipt_hash: str,
    approval_timestamp: datetime | None = None,
    notes: str = "Live authorization granted following Phase 7 readiness audit.",
    secret_salt: str = "TOSO-LIVE-ARMING-AUTH-v1",
) -> HumanApprovalToken:
    """Helper for human owner to generate an authenticated HumanApprovalToken."""
    if not approver_id or not approver_id.strip():
        raise InvalidApprovalTokenError("approver_id must be a non-empty string.")

    ts = approval_timestamp or datetime.now(UTC)
    signature = compute_approval_token_signature(
        approver_id=approver_id,
        approval_timestamp=ts,
        config_version=config_version,
        acceptance_matrix_digest=acceptance_matrix_digest,
        preflight_receipt_hash=preflight_receipt_hash,
        secret_salt=secret_salt,
    )
    return HumanApprovalToken(
        approver_id=approver_id.strip(),
        approval_timestamp=ts,
        config_version=config_version.strip(),
        acceptance_matrix_digest=acceptance_matrix_digest.strip(),
        preflight_receipt_hash=preflight_receipt_hash.strip(),
        signature=signature,
        notes=notes,
    )


class LiveArmingGate:
    """Authoritative gate controlling live order dispatch authorization."""

    def __init__(self) -> None:
        self._mode: LiveDeploymentMode = LiveDeploymentMode.SIMULATION
        self._is_armed: bool = False
        self._current_receipt: LiveArmingReceipt | None = None

    @property
    def mode(self) -> LiveDeploymentMode:
        """Return the current deployment mode."""
        return self._mode

    @property
    def is_armed(self) -> bool:
        """Return True if system is actively armed for live execution."""
        return self._is_armed

    @property
    def current_receipt(self) -> LiveArmingReceipt | None:
        """Return active arming audit receipt if armed, else None."""
        return self._current_receipt

    def arm_system(
        self,
        human_token: HumanApprovalToken,
        preflight_receipt: PreFlightCheckReceipt,
        matrix_receipt: AcceptanceMatrixAuditReceipt,
        readiness_receipt: LiveReadinessAuditReceipt,
        arming_timestamp: datetime | None = None,
        secret_salt: str = "TOSO-LIVE-ARMING-AUTH-v1",
        runner: Any | None = None,
    ) -> LiveArmingReceipt:
        """Verify prerequisites, validate human approval signature, and arm system.

        Args:
            human_token: Authenticated HumanApprovalToken.
            preflight_receipt: Verified PreFlightCheckReceipt.
            matrix_receipt: Verified AcceptanceMatrixAuditReceipt.
            readiness_receipt: Verified LiveReadinessAuditReceipt.
            arming_timestamp: Optional explicit timestamp for deterministic testing.
            secret_salt: Secret key string for token signature verification.
            runner: Optional simulation or live runner to register arming.

        Returns:
            LiveArmingReceipt with cryptographic arming digest.

        Raises:
            InvalidApprovalTokenError: If token signature or digests mismatch.
            LiveArmingError: If pre-flight or matrix checks are incomplete.
        """
        now = arming_timestamp or datetime.now(UTC)

        # 1. Validate Pre-Flight Readiness
        if not preflight_receipt.all_passed:
            raise LiveArmingError(
                "Cannot arm live system: PreFlightCheckReceipt reported failing checks."
            )

        # 2. Validate Acceptance Matrix Completeness
        if not matrix_receipt.is_complete:
            raise LiveArmingError(
                "Cannot arm live system: Acceptance Matrix is incomplete or unverified."
            )

        # 3. Validate Open Questions Resolution
        if not readiness_receipt.all_blocking_resolved:
            raise LiveArmingError(
                "Cannot arm live system: Unresolved blocking open questions remain."
            )

        # 4. Validate Token Alignment
        if human_token.config_version != readiness_receipt.config_version:
            raise InvalidApprovalTokenError(
                f"Token config version '{human_token.config_version}' does not match "
                f"readiness config version '{readiness_receipt.config_version}'."
            )
        if human_token.acceptance_matrix_digest != matrix_receipt.matrix_digest:
            raise InvalidApprovalTokenError(
                "Token acceptance matrix digest does not match verified matrix receipt."
            )
        if human_token.preflight_receipt_hash != preflight_receipt.receipt_hash:
            raise InvalidApprovalTokenError(
                "Token preflight receipt hash does not match verified preflight receipt."
            )

        # 5. Verify Cryptographic Token Signature
        expected_sig = compute_approval_token_signature(
            approver_id=human_token.approver_id,
            approval_timestamp=human_token.approval_timestamp,
            config_version=human_token.config_version,
            acceptance_matrix_digest=human_token.acceptance_matrix_digest,
            preflight_receipt_hash=human_token.preflight_receipt_hash,
            secret_salt=secret_salt,
        )
        if human_token.signature != expected_sig:
            raise InvalidApprovalTokenError(
                "Human approval token cryptographic signature verification failed."
            )

        # 6. Check runner halt status if runner provided
        if runner is not None and getattr(runner, "is_halted", False):
            raise LiveArmingError(
                "Cannot arm live system while runner is in emergency HALTED state."
            )

        # 7. Generate Sealed Arming Receipt
        arming_id = f"ARM-{uuid.uuid4().hex[:12]}"
        digest_payload = {
            "arming_id": arming_id,
            "mode": LiveDeploymentMode.LIVE_ACTIVE.value,
            "armed_at": now.isoformat(),
            "approver_id": human_token.approver_id,
            "token_signature": human_token.signature,
            "preflight_hash": preflight_receipt.receipt_hash,
            "matrix_digest": matrix_receipt.matrix_digest,
        }
        encoded = json.dumps(digest_payload, sort_keys=True).encode("utf-8")
        arming_digest = hashlib.sha256(encoded).hexdigest()

        receipt = LiveArmingReceipt(
            arming_id=arming_id,
            mode=LiveDeploymentMode.LIVE_ACTIVE,
            armed_at=now,
            human_approval=human_token,
            preflight_receipt=preflight_receipt,
            matrix_receipt=matrix_receipt,
            readiness_receipt=readiness_receipt,
            is_armed=True,
            arming_digest=arming_digest,
        )

        self._mode = LiveDeploymentMode.LIVE_ACTIVE
        self._is_armed = True
        self._current_receipt = receipt

        return receipt

    def disarm_system(self, reason: str = "MANUAL_DISARM") -> None:
        """Immediately revoke live arming and return to safe SIMULATION mode."""
        self._mode = LiveDeploymentMode.SIMULATION
        self._is_armed = False
        self._current_receipt = None

    def verify_order_dispatch_authorized(
        self,
        current_time: datetime | None = None,
        max_session_hours: float = 24.0,
        runner: Any | None = None,
    ) -> None:
        """Verify that live order dispatch is currently authorized.

        Args:
            current_time: Optional explicit timestamp.
            max_session_hours: Maximum lifetime of an arming session (default 24h).
            runner: Optional runner to verify unhalted state.

        Raises:
            UnauthorizedLiveDispatchError: If gate is disarmed or not in LIVE_ACTIVE mode.
            ExpiredArmingSessionError: If the arming session has expired.
            LiveArmingError: If the runner is halted.
        """
        if not self._is_armed or self._mode != LiveDeploymentMode.LIVE_ACTIVE:
            raise UnauthorizedLiveDispatchError(
                "Live order dispatch blocked: system is NOT armed for live trading."
            )

        if self._current_receipt is None:
            raise UnauthorizedLiveDispatchError(
                "Live order dispatch blocked: missing active arming receipt."
            )

        now = current_time or datetime.now(UTC)
        elapsed_seconds = (now - self._current_receipt.armed_at).total_seconds()
        if elapsed_seconds > max_session_hours * 3600:
            self.disarm_system(reason="SESSION_EXPIRED")
            hrs = elapsed_seconds / 3600
            raise ExpiredArmingSessionError(
                f"Live arming session expired ({hrs:.2f}h > {max_session_hours}h)."
            )

        if runner is not None and getattr(runner, "is_halted", False):
            self.disarm_system(reason="RUNNER_HALTED")
            raise LiveArmingError(
                "Live order dispatch blocked: runner has entered emergency HALTED state."
            )
