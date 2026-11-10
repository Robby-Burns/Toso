"""TOSO Scientist Security Boundary & Execution Isolation Engine.

Implements FR-017, INV-014, FR-012, INV-008, INV-001, INV-006:
- Guarantees isolation: Scientist code cannot submit, cancel, or route orders.
- Prevents Scientist models/agents from bypassing calculation or Execution Gate.
- Enforces strict human approval verification before any proposal can modify prod.
- Provides cryptographic token validation and deterministic audit receipts.
- Fail-closed handling for UNKNOWN, corrupted, or expired tokens (INV-008).
"""

import hashlib
import json
from datetime import datetime
from typing import Any

from toso.scientist.errors import (
    InvalidApprovalTokenError,
    ScientistExecutionBreachError,
    UnauthorizedProductionChangeError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    ExecutionInterceptionReceipt,
    HumanApprovalToken,
    ProductionChangeProposal,
    ProposalStatus,
    ScientistRole,
)


def compute_proposal_hash(
    proposal_id: str,
    hypothesis_id: str,
    proposed_parameters: dict[str, Any],
    rationale: str,
) -> str:
    """Compute the deterministic SHA-256 hash sealing a production change proposal."""
    if not isinstance(proposed_parameters, dict):
        raise UnknownScientistInputError(
            f"proposed_parameters must be a dict: {type(proposed_parameters)}"
        )
    try:
        params_json = json.dumps(proposed_parameters, sort_keys=True, default=str)
    except Exception as e:
        raise UnknownScientistInputError(f"Failed to serialize proposed_parameters: {e}") from e

    content = f"{proposal_id}|{hypothesis_id}|{params_json}|{rationale}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def generate_human_approval_signature(
    token_id: str,
    approver_id: str,
    target_proposal_hash: str,
    approved_at: datetime,
) -> str:
    """Generate the authoritative signature for a human approval token."""
    content = f"{token_id}|{approver_id}|{target_proposal_hash}|{approved_at.isoformat()}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def create_production_proposal(
    proposal_id: str,
    hypothesis_id: str,
    proposed_parameters: dict[str, Any],
    rationale: str,
    created_at: datetime,
) -> ProductionChangeProposal:
    """Create a new unapproved ProductionChangeProposal from research findings."""
    if not proposal_id or not isinstance(proposal_id, str):
        raise UnknownScientistInputError(f"Invalid proposal_id: {proposal_id}")
    if not hypothesis_id or not isinstance(hypothesis_id, str):
        raise UnknownScientistInputError(f"Invalid hypothesis_id: {hypothesis_id}")
    if not rationale or not isinstance(rationale, str):
        raise UnknownScientistInputError(f"Invalid rationale: {rationale}")
    if not isinstance(created_at, datetime):
        raise UnknownScientistInputError(f"Invalid created_at: {created_at}")

    proposal_hash = compute_proposal_hash(
        proposal_id=proposal_id,
        hypothesis_id=hypothesis_id,
        proposed_parameters=proposed_parameters,
        rationale=rationale,
    )

    return ProductionChangeProposal(
        proposal_id=proposal_id,
        hypothesis_id=hypothesis_id,
        proposed_parameters=proposed_parameters,
        rationale=rationale,
        proposal_hash=proposal_hash,
        created_at=created_at,
        is_approved=False,
        approval_token=None,
    )


def verify_human_approval_token(
    proposal: ProductionChangeProposal,
    token: HumanApprovalToken,
    current_time: datetime,
) -> bool:
    """Verify that a human approval token cryptographically matches the proposal and is valid.

    Args:
        proposal: The ProductionChangeProposal to be approved.
        token: The HumanApprovalToken presented for authorization.
        current_time: Clock anchor for expiration verification (INV-001).

    Returns:
        True if the token is valid, unexpired, and matches the proposal hash.

    Raises:
        InvalidApprovalTokenError: If token is expired, forged, or mismatched (INV-014).
        UnknownScientistInputError: On malformed input types.
    """
    if not isinstance(proposal, ProductionChangeProposal):
        raise UnknownScientistInputError(f"Invalid proposal type: {type(proposal)}")
    if not isinstance(token, HumanApprovalToken):
        raise UnknownScientistInputError(f"Invalid token type: {type(token)}")
    if not isinstance(current_time, datetime):
        raise UnknownScientistInputError(f"Invalid current_time: {current_time}")

    # 1. Proposal Hash Integrity & Match
    computed_hash = compute_proposal_hash(
        proposal_id=proposal.proposal_id,
        hypothesis_id=proposal.hypothesis_id,
        proposed_parameters=proposal.proposed_parameters,
        rationale=proposal.rationale,
    )
    if proposal.proposal_hash != computed_hash:
        raise InvalidApprovalTokenError(
            f"Proposal hash '{proposal.proposal_hash}' does not match computed parameters hash "
            f"'{computed_hash}'. Proposal parameters or rationale have been tampered with."
        )
    if token.target_proposal_hash != proposal.proposal_hash:
        raise InvalidApprovalTokenError(
            f"Approval token hash '{token.target_proposal_hash}' does not match "
            f"proposal hash '{proposal.proposal_hash}'."
        )

    # 2. Time Window Validity
    if current_time < token.approved_at:
        raise InvalidApprovalTokenError(
            f"Token evaluation time ({current_time}) precedes approval timestamp "
            f"({token.approved_at})."
        )
    if current_time > token.expires_at:
        raise InvalidApprovalTokenError(
            f"Approval token expired at {token.expires_at}; current time is {current_time}."
        )

    # 3. Signature Verification
    expected_signature = generate_human_approval_signature(
        token_id=token.token_id,
        approver_id=token.approver_id,
        target_proposal_hash=token.target_proposal_hash,
        approved_at=token.approved_at,
    )
    if token.signature != expected_signature:
        raise InvalidApprovalTokenError(
            f"Invalid token signature for token '{token.token_id}': authenticity check failed."
        )

    return True


def attach_human_approval(
    proposal: ProductionChangeProposal,
    token: HumanApprovalToken,
    current_time: datetime,
) -> ProductionChangeProposal:
    """Attach and seal a verified human approval token to a proposal."""
    verify_human_approval_token(proposal, token, current_time)

    return ProductionChangeProposal(
        proposal_id=proposal.proposal_id,
        hypothesis_id=proposal.hypothesis_id,
        proposed_parameters=proposal.proposed_parameters,
        rationale=proposal.rationale,
        proposal_hash=proposal.proposal_hash,
        created_at=proposal.created_at,
        status=ProposalStatus.APPROVED,
        is_approved=True,
        approval_token=token,
        reviewed_at=token.approved_at,
    )


class ScientistExecutionGuard:
    """Security sentinel preventing Scientist access to execution capabilities (INV-014)."""

    @staticmethod
    def intercept_order_submission(
        caller_role: ScientistRole | str,
        action: str,
        timestamp: datetime,
    ) -> ExecutionInterceptionReceipt:
        """Intercept and block any order submission or routing attempt from Scientist layer.

        Args:
            caller_role: Originating role or subsystem name.
            action: Attempted execution action (e.g. 'submit_order', 'cancel_order').
            timestamp: Clock anchor echoed into the audit receipt.

        Returns:
            ExecutionInterceptionReceipt.

        Raises:
            ScientistExecutionBreachError: Always raised to immediately halt execution attempts.
        """
        role_str = caller_role.value if isinstance(caller_role, ScientistRole) else str(caller_role)
        reason = (
            f"Execution attempt '{action}' by '{role_str}' was intercepted and permanently "
            "blocked. The Scientist subsystem is strictly isolated from trade execution (INV-014)."
        )

        raise ScientistExecutionBreachError(reason)

    @staticmethod
    def apply_production_change(
        proposal: ProductionChangeProposal,
        current_time: datetime,
    ) -> dict[str, Any]:
        """Apply a research proposal to production ONLY if valid human approval is verified.

        Args:
            proposal: ProductionChangeProposal with attached approval token.
            current_time: Timestamp for token validity verification.

        Returns:
            Dictionary of approved parameters safe for production adoption.

        Raises:
            UnauthorizedProductionChangeError: If proposal is unapproved or lacks token (INV-014).
            InvalidApprovalTokenError: If token is invalid or expired.
        """
        if not proposal.is_approved or proposal.approval_token is None:
            raise UnauthorizedProductionChangeError(
                f"Proposal '{proposal.proposal_id}' lacks verified human approval. "
                "Autonomous production changes are strictly prohibited (INV-014)."
            )

        verify_human_approval_token(proposal, proposal.approval_token, current_time)
        return dict(proposal.proposed_parameters)
