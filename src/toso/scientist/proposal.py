"""TOSO Scientist Production Proposal & Human Approval Workflow.

Coordinates the transition of verified research into versioned production changes:
- Formulates proposals grounded in validated OOS evaluations (FR-017, FR-018, FR-019).
- Enforces strict proposal lifecycle state transitions (FR-019, INV-014).
- Requires cryptographic HumanApprovalToken verification before approval (INV-014, INV-015).
- Executes atomic adoption into versioned CoreConfig contracts (INV-015).
- Deterministic proposal registry with disk persistence and receipts (INV-001, INV-006, INV-008).
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from toso.config.models import ApprovalStatus, CoreConfig, VersionMetadata
from toso.scientist.boundary import (
    compute_proposal_hash,
    verify_human_approval_token,
)
from toso.scientist.errors import (
    DuplicateProposalError,
    InvalidApprovalTokenError,
    InvalidProposalStateTransitionError,
    JournalSerializationError,
    ProposalNotFoundError,
    UnauthorizedProductionChangeError,
    UnknownScientistInputError,
    UnvalidatedProposalError,
)
from toso.scientist.models import (
    HumanApprovalToken,
    OOSEvaluationResult,
    ProductionChangeProposal,
    ProposalReceipt,
    ProposalStatus,
    ValidationVerdict,
)

LEGAL_PROPOSAL_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.DRAFT: {
        ProposalStatus.SUBMITTED_FOR_REVIEW,
        ProposalStatus.WITHDRAWN,
    },
    ProposalStatus.SUBMITTED_FOR_REVIEW: {
        ProposalStatus.APPROVED,
        ProposalStatus.REJECTED,
        ProposalStatus.WITHDRAWN,
    },
    ProposalStatus.APPROVED: {
        ProposalStatus.ADOPTED,
        ProposalStatus.WITHDRAWN,
    },
    ProposalStatus.ADOPTED: set(),  # Terminal state
    ProposalStatus.REJECTED: {
        ProposalStatus.DRAFT,  # Can return to draft for revision
        ProposalStatus.WITHDRAWN,
    },
    ProposalStatus.WITHDRAWN: set(),  # Terminal state
}


def compute_proposal_receipt_checksum(
    receipt_id: str,
    proposal_id: str,
    action: str,
    new_status: ProposalStatus,
    timestamp: datetime,
) -> str:
    """Compute deterministic SHA-256 seal for proposal lifecycle receipts."""
    content = f"{receipt_id}|{proposal_id}|{action}|{new_status.value}|{timestamp.isoformat()}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def formulate_proposal_from_oos(
    proposal_id: str,
    oos_result: OOSEvaluationResult,
    proposed_parameters: dict[str, Any],
    rationale: str,
    created_at: datetime,
) -> ProductionChangeProposal:
    """Formulate a new ProductionChangeProposal grounded in validated OOS evaluation results.

    Args:
        proposal_id: Unique proposal identifier.
        oos_result: Verified OOSEvaluationResult from held-out validation.
        proposed_parameters: Dictionary of proposed parameter changes.
        rationale: Evidence-backed reasoning for the production change.
        created_at: Formulation timestamp anchor.

    Returns:
        ProductionChangeProposal in initial DRAFT status.

    Raises:
        UnvalidatedProposalError: If OOS result is not VALIDATED or lacks proposal approval.
        UnknownScientistInputError: On malformed input arguments.
    """
    if not isinstance(oos_result, OOSEvaluationResult):
        raise UnknownScientistInputError(
            f"oos_result must be an OOSEvaluationResult, got {type(oos_result)}"
        )
    if oos_result.verdict != ValidationVerdict.VALIDATED or not oos_result.is_approved_for_proposal:
        raise UnvalidatedProposalError(
            f"Cannot formulate proposal from unvalidated OOS eval {oos_result.evaluation_id}: "
            f"verdict is {oos_result.verdict.value} "
            f"(is_approved={oos_result.is_approved_for_proposal})."
        )
    if not proposal_id or not isinstance(proposal_id, str):
        raise UnknownScientistInputError(f"Invalid proposal_id: {proposal_id}")
    if not isinstance(proposed_parameters, dict) or not proposed_parameters:
        raise UnknownScientistInputError("proposed_parameters must be a non-empty dictionary.")
    if not rationale or not isinstance(rationale, str):
        raise UnknownScientistInputError("rationale must be a non-empty string.")
    if not isinstance(created_at, datetime):
        raise UnknownScientistInputError(f"Invalid created_at timestamp: {created_at}")

    prop_hash = compute_proposal_hash(
        proposal_id=proposal_id,
        hypothesis_id=oos_result.hypothesis_id,
        proposed_parameters=proposed_parameters,
        rationale=rationale,
    )

    return ProductionChangeProposal(
        proposal_id=proposal_id,
        hypothesis_id=oos_result.hypothesis_id,
        proposed_parameters=proposed_parameters,
        rationale=rationale,
        proposal_hash=prop_hash,
        created_at=created_at,
        status=ProposalStatus.DRAFT,
        is_approved=False,
        approval_token=None,
    )


def transition_proposal_status(
    proposal: ProductionChangeProposal,
    new_status: ProposalStatus,
    timestamp: datetime,
    reason: str = "",
    token: HumanApprovalToken | None = None,
) -> ProductionChangeProposal:
    """Transition a proposal to a new lifecycle status following strict state machine rules.

    Args:
        proposal: Current immutable ProductionChangeProposal.
        new_status: Target ProposalStatus.
        timestamp: Evaluation clock anchor echoed into transition.
        reason: Optional audit notes or rejection rationale.
        token: Mandatory HumanApprovalToken when transitioning to APPROVED.

    Returns:
        Updated immutable ProductionChangeProposal.

    Raises:
        InvalidProposalStateTransitionError: If transition is illegal per state machine.
        InvalidApprovalTokenError: If token is missing, invalid, or expired on approval.
        UnknownScientistInputError: On invalid input types.
    """
    if not isinstance(proposal, ProductionChangeProposal):
        raise UnknownScientistInputError(f"Invalid proposal type: {type(proposal)}")
    if not isinstance(new_status, ProposalStatus):
        raise UnknownScientistInputError(f"Invalid new_status: {new_status}")
    if not isinstance(timestamp, datetime):
        raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

    allowed_targets = LEGAL_PROPOSAL_TRANSITIONS.get(proposal.status, set())
    if new_status not in allowed_targets:
        raise InvalidProposalStateTransitionError(
            f"Cannot transition proposal '{proposal.proposal_id}' from status "
            f"'{proposal.status.value}' to '{new_status.value}'. "
            f"Allowed transitions: {[s.value for s in allowed_targets]}."
        )

    # 1. Transition to APPROVED: Requires cryptographic HumanApprovalToken
    if new_status == ProposalStatus.APPROVED:
        if token is None or not isinstance(token, HumanApprovalToken):
            raise InvalidApprovalTokenError(
                f"Transitioning proposal '{proposal.proposal_id}' to APPROVED "
                "requires a valid HumanApprovalToken (INV-014, INV-015)."
            )
        verify_human_approval_token(proposal, token, timestamp)

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
            rejection_reason=None,
        )

    # 2. Transition to REJECTED
    if new_status == ProposalStatus.REJECTED:
        return ProductionChangeProposal(
            proposal_id=proposal.proposal_id,
            hypothesis_id=proposal.hypothesis_id,
            proposed_parameters=proposal.proposed_parameters,
            rationale=proposal.rationale,
            proposal_hash=proposal.proposal_hash,
            created_at=proposal.created_at,
            status=ProposalStatus.REJECTED,
            is_approved=False,
            approval_token=None,
            reviewed_at=timestamp,
            rejection_reason=reason or "Rejected by reviewer.",
        )

    # 3. Transition to ADOPTED: Must already be APPROVED with valid token
    if new_status == ProposalStatus.ADOPTED:
        if not proposal.is_approved or proposal.approval_token is None:
            raise UnauthorizedProductionChangeError(
                f"Cannot adopt proposal '{proposal.proposal_id}' without verified approval."
            )
        verify_human_approval_token(proposal, proposal.approval_token, timestamp)

        return ProductionChangeProposal(
            proposal_id=proposal.proposal_id,
            hypothesis_id=proposal.hypothesis_id,
            proposed_parameters=proposal.proposed_parameters,
            rationale=proposal.rationale,
            proposal_hash=proposal.proposal_hash,
            created_at=proposal.created_at,
            status=ProposalStatus.ADOPTED,
            is_approved=True,
            approval_token=proposal.approval_token,
            reviewed_at=proposal.reviewed_at,
            adopted_at=timestamp,
            rejection_reason=None,
        )

    # 4. Standard Transitions (SUBMITTED_FOR_REVIEW, WITHDRAWN, DRAFT reset)
    return ProductionChangeProposal(
        proposal_id=proposal.proposal_id,
        hypothesis_id=proposal.hypothesis_id,
        proposed_parameters=proposal.proposed_parameters,
        rationale=proposal.rationale,
        proposal_hash=proposal.proposal_hash,
        created_at=proposal.created_at,
        status=new_status,
        is_approved=False,
        approval_token=None,
        reviewed_at=proposal.reviewed_at,
        adopted_at=None,
        rejection_reason=None,
    )


def adopt_proposal_to_config(
    proposal: ProductionChangeProposal,
    current_config: CoreConfig,
    new_version: str,
    adoption_time: datetime,
) -> tuple[CoreConfig, ProductionChangeProposal, ProposalReceipt]:
    """Atomically adopt an approved research proposal into a new versioned CoreConfig.

    Args:
        proposal: Approved ProductionChangeProposal with valid human approval token.
        current_config: Active CoreConfig contract being updated.
        new_version: New semantic version string for adopted config (e.g. '1.1.0').
        adoption_time: Timestamp of adoption.

    Returns:
        Tuple of (adopted_core_config, adopted_proposal, proposal_receipt).

    Raises:
        UnauthorizedProductionChangeError: If proposal is not approved or unverified.
        InvalidApprovalTokenError: If approval token is expired or mismatched.
        UnknownScientistInputError: On malformed input types.
    """
    if not isinstance(proposal, ProductionChangeProposal):
        raise UnknownScientistInputError(f"Invalid proposal type: {type(proposal)}")
    if not isinstance(current_config, CoreConfig):
        raise UnknownScientistInputError(f"Invalid current_config type: {type(current_config)}")
    if not new_version or not isinstance(new_version, str):
        raise UnknownScientistInputError(f"Invalid new_version: {new_version}")
    if not isinstance(adoption_time, datetime):
        raise UnknownScientistInputError(f"Invalid adoption_time: {adoption_time}")

    if (
        proposal.status != ProposalStatus.APPROVED
        or not proposal.is_approved
        or proposal.approval_token is None
    ):
        raise UnauthorizedProductionChangeError(
            f"Proposal '{proposal.proposal_id}' cannot be adopted: status is "
            f"'{proposal.status.value}' and requires verified APPROVED status (INV-014, INV-015)."
        )

    verify_human_approval_token(proposal, proposal.approval_token, adoption_time)

    # 1. Update StrategyConfig parameters
    updated_strategy = current_config.strategy.model_copy(update=proposal.proposed_parameters)

    # 2. Update VersionMetadata with human approver audit trail
    updated_metadata = VersionMetadata(
        version=new_version,
        status=ApprovalStatus.APPROVED,
        approved_by=proposal.approval_token.approver_id,
        approved_at=proposal.approval_token.approved_at.isoformat(),
        checksum=proposal.proposal_hash,
        description=f"Adopted proposal {proposal.proposal_id}: {proposal.rationale}",
    )

    # 3. Construct adopted CoreConfig
    adopted_config = current_config.model_copy(
        update={"metadata": updated_metadata, "strategy": updated_strategy}
    )

    # 4. Transition Proposal to ADOPTED
    adopted_proposal = transition_proposal_status(
        proposal=proposal,
        new_status=ProposalStatus.ADOPTED,
        timestamp=adoption_time,
    )

    # 5. Formulate ProposalReceipt
    receipt_id = f"RCPT-PROP-{proposal.proposal_id}-ADOPTED"
    checksum = compute_proposal_receipt_checksum(
        receipt_id=receipt_id,
        proposal_id=proposal.proposal_id,
        action="PROPOSAL_ADOPTED_TO_PRODUCTION",
        new_status=ProposalStatus.ADOPTED,
        timestamp=adoption_time,
    )

    receipt = ProposalReceipt(
        receipt_id=receipt_id,
        proposal_id=proposal.proposal_id,
        action="PROPOSAL_ADOPTED_TO_PRODUCTION",
        previous_status=ProposalStatus.APPROVED,
        new_status=ProposalStatus.ADOPTED,
        timestamp=adoption_time,
        checksum=checksum,
        details=f"Adopted into CoreConfig version {new_version}.",
    )

    return adopted_config, adopted_proposal, receipt


class ProposalRegistry:
    """Deterministic repository for production change proposals with audit receipts."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._proposals: dict[str, ProductionChangeProposal] = {}
        self.storage_path = storage_path
        if storage_path is not None and storage_path.exists():
            self._load_from_disk()

    def register_proposal(
        self,
        proposal: ProductionChangeProposal,
        timestamp: datetime,
    ) -> ProposalReceipt:
        """Register a new production change proposal."""
        if not isinstance(proposal, ProductionChangeProposal):
            raise UnknownScientistInputError(f"Invalid proposal type: {type(proposal)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        if proposal.proposal_id in self._proposals:
            raise DuplicateProposalError(
                f"Proposal '{proposal.proposal_id}' is already registered."
            )

        self._proposals[proposal.proposal_id] = proposal

        if self.storage_path is not None:
            self._persist_to_disk()

        receipt_id = f"RCPT-PROP-{proposal.proposal_id}-REG"
        checksum = compute_proposal_receipt_checksum(
            receipt_id=receipt_id,
            proposal_id=proposal.proposal_id,
            action="PROPOSAL_REGISTERED",
            new_status=proposal.status,
            timestamp=timestamp,
        )

        return ProposalReceipt(
            receipt_id=receipt_id,
            proposal_id=proposal.proposal_id,
            action="PROPOSAL_REGISTERED",
            previous_status=None,
            new_status=proposal.status,
            timestamp=timestamp,
            checksum=checksum,
            details=f"Registered proposal {proposal.proposal_id}.",
        )

    def get_proposal(self, proposal_id: str) -> ProductionChangeProposal:
        """Retrieve a registered proposal by ID."""
        if not proposal_id or not isinstance(proposal_id, str):
            raise UnknownScientistInputError(f"Invalid proposal_id: {proposal_id}")
        if proposal_id not in self._proposals:
            raise ProposalNotFoundError(f"Proposal '{proposal_id}' was not found in registry.")
        return self._proposals[proposal_id]

    def update_proposal(
        self,
        proposal: ProductionChangeProposal,
        timestamp: datetime,
    ) -> ProposalReceipt:
        """Update an existing proposal."""
        if not isinstance(proposal, ProductionChangeProposal):
            raise UnknownScientistInputError(f"Invalid proposal type: {type(proposal)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        if proposal.proposal_id not in self._proposals:
            raise ProposalNotFoundError(
                f"Cannot update unrecorded proposal '{proposal.proposal_id}'."
            )

        prev_status = self._proposals[proposal.proposal_id].status
        self._proposals[proposal.proposal_id] = proposal

        if self.storage_path is not None:
            self._persist_to_disk()

        receipt_id = f"RCPT-PROP-{proposal.proposal_id}-UPD"
        checksum = compute_proposal_receipt_checksum(
            receipt_id=receipt_id,
            proposal_id=proposal.proposal_id,
            action="PROPOSAL_UPDATED",
            new_status=proposal.status,
            timestamp=timestamp,
        )

        return ProposalReceipt(
            receipt_id=receipt_id,
            proposal_id=proposal.proposal_id,
            action="PROPOSAL_UPDATED",
            previous_status=prev_status,
            new_status=proposal.status,
            timestamp=timestamp,
            checksum=checksum,
            details=f"Updated proposal {proposal.proposal_id} to status {proposal.status.value}.",
        )

    def query_proposals(
        self,
        status: ProposalStatus | None = None,
        hypothesis_id: str | None = None,
    ) -> tuple[ProductionChangeProposal, ...]:
        """Query proposals deterministically sorted by proposal_id."""
        results: list[ProductionChangeProposal] = []
        for prop in self._proposals.values():
            if status is not None and prop.status != status:
                continue
            if hypothesis_id is not None and prop.hypothesis_id != hypothesis_id:
                continue
            results.append(prop)

        return tuple(sorted(results, key=lambda p: p.proposal_id))

    def _persist_to_disk(self) -> None:
        """Persist registered proposals to disk in JSONL format."""
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            for prop in sorted(self._proposals.values(), key=lambda p: p.proposal_id):
                f.write(prop.model_dump_json() + "\n")

    def _load_from_disk(self) -> None:
        """Load proposals from JSONL file on disk."""
        if self.storage_path is None or not self.storage_path.exists():
            return
        with open(self.storage_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                    prop = ProductionChangeProposal.model_validate(data)
                    self._proposals[prop.proposal_id] = prop
                except Exception as e:
                    raise JournalSerializationError(
                        f"Failed to deserialize proposal at line {line_no}: {e}"
                    ) from e
