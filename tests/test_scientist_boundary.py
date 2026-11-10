"""Tests for Story SCI-002: Scientist Execution Boundary & Security Isolation.

Verifies:
- FR-017: Scientist research isolation without trade execution authority.
- INV-014: No Autonomous Production Change without explicit human approval.
- FR-012: Guardian Execution Gate authority cannot be subverted by Scientist outputs.
- INV-008: Fail-closed handling on corrupt, missing, or invalid inputs.
- INV-001, INV-006: Pure determinism and cryptographic audit receipts.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.scientist import (
    HumanApprovalToken,
    InvalidApprovalTokenError,
    ProductionChangeProposal,
    ScientistExecutionBreachError,
    ScientistExecutionGuard,
    ScientistRole,
    UnauthorizedProductionChangeError,
    UnknownScientistInputError,
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
    verify_human_approval_token,
)


@pytest.fixture
def current_time() -> datetime:
    return datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC)


@pytest.fixture
def valid_proposal(current_time: datetime) -> ProductionChangeProposal:
    return create_production_proposal(
        proposal_id="PROP-2026-001",
        hypothesis_id="HYP-VOLATILITY-01",
        proposed_parameters={"breakout_lookback_bars": 25, "atr_period": 14},
        rationale="Empirical backtesting indicates 25-bar lookback reduces false breakouts.",
        created_at=current_time,
    )


def test_ac001_scientist_order_execution_intercepted_and_blocked(
    current_time: datetime,
) -> None:
    """AC-001: Scientist tools cannot submit or route orders (INV-014)."""
    # Attempting to submit or cancel orders from researcher context
    actions_to_test = ["submit_order", "cancel_order", "route_market_order", "liquidate_all"]

    for action in actions_to_test:
        with pytest.raises(ScientistExecutionBreachError) as exc_info:
            ScientistExecutionGuard.intercept_order_submission(
                caller_role=ScientistRole.RESEARCHER,
                action=action,
                timestamp=current_time,
            )
        assert "permanently blocked" in str(exc_info.value)
        assert "strictly isolated" in str(exc_info.value)


def test_ac002_autonomous_production_change_rejected(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """AC-002: Autonomous parameter changes raise UnauthorizedProductionChangeError."""
    assert valid_proposal.is_approved is False
    assert valid_proposal.approval_token is None

    with pytest.raises(UnauthorizedProductionChangeError) as exc_info:
        ScientistExecutionGuard.apply_production_change(
            proposal=valid_proposal,
            current_time=current_time,
        )
    assert "strictly prohibited" in str(exc_info.value)
    assert "lacks verified human approval" in str(exc_info.value)


def test_ac003_valid_human_approval_token_allows_adoption(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """AC-003: Valid, unexpired human approval token successfully authorizes production change."""
    token_id = "TOK-AUTH-001"
    approver = "Owner-Alice"
    approved_at = current_time
    expires_at = current_time + timedelta(hours=24)

    sig = generate_human_approval_signature(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=valid_proposal.proposal_hash,
        approved_at=approved_at,
    )

    token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=valid_proposal.proposal_hash,
        signature=sig,
        approved_at=approved_at,
        expires_at=expires_at,
    )

    # Verification passes
    assert verify_human_approval_token(valid_proposal, token, current_time) is True

    # Attach token to proposal
    approved_proposal = attach_human_approval(valid_proposal, token, current_time)
    assert approved_proposal.is_approved is True
    assert approved_proposal.approval_token == token

    # Applying change succeeds
    adopted_params = ScientistExecutionGuard.apply_production_change(
        proposal=approved_proposal,
        current_time=current_time,
    )
    assert adopted_params["breakout_lookback_bars"] == 25
    assert adopted_params["atr_period"] == 14


def test_ac004_mismatched_proposal_hash_rejected(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """AC-004: Approval token targeting a different proposal hash is rejected."""
    token_id = "TOK-AUTH-002"
    approver = "Owner-Alice"
    wrong_hash = "deadbeef" * 8

    sig = generate_human_approval_signature(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=wrong_hash,
        approved_at=current_time,
    )

    token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=wrong_hash,
        signature=sig,
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=24),
    )

    with pytest.raises(InvalidApprovalTokenError) as exc_info:
        verify_human_approval_token(valid_proposal, token, current_time)
    assert "does not match proposal hash" in str(exc_info.value)


def test_ac005_expired_approval_token_rejected(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """AC-005: Expired human approval token raises InvalidApprovalTokenError."""
    token_id = "TOK-AUTH-003"
    approver = "Owner-Alice"
    approved_at = current_time - timedelta(days=2)
    expires_at = current_time - timedelta(hours=1)  # Expired 1 hour ago

    sig = generate_human_approval_signature(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=valid_proposal.proposal_hash,
        approved_at=approved_at,
    )

    expired_token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=valid_proposal.proposal_hash,
        signature=sig,
        approved_at=approved_at,
        expires_at=expires_at,
    )

    with pytest.raises(InvalidApprovalTokenError) as exc_info:
        verify_human_approval_token(valid_proposal, expired_token, current_time)
    assert "Approval token expired" in str(exc_info.value)


def test_ac006_forged_signature_rejected(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """AC-006: Forged or altered signature raises InvalidApprovalTokenError."""
    token = HumanApprovalToken(
        token_id="TOK-AUTH-004",
        approver_id="Owner-Alice",
        target_proposal_hash=valid_proposal.proposal_hash,
        signature="bad_signature_hex_12345",
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=24),
    )

    with pytest.raises(InvalidApprovalTokenError) as exc_info:
        verify_human_approval_token(valid_proposal, token, current_time)
    assert "Invalid token signature" in str(exc_info.value)


def test_fail_closed_on_corrupt_unknown_inputs(
    current_time: datetime,
) -> None:
    """INV-008: Fail-closed on missing, empty, or wrong input types."""
    # Bad proposal parameters
    with pytest.raises(UnknownScientistInputError):
        create_production_proposal(
            proposal_id="",
            hypothesis_id="HYP-1",
            proposed_parameters={},
            rationale="Rationale",
            created_at=current_time,
        )

    with pytest.raises(UnknownScientistInputError):
        create_production_proposal(
            proposal_id="PROP-1",
            hypothesis_id="",
            proposed_parameters={},
            rationale="Rationale",
            created_at=current_time,
        )

    with pytest.raises(UnknownScientistInputError):
        create_production_proposal(
            proposal_id="PROP-1",
            hypothesis_id="HYP-1",
            proposed_parameters="not_a_dict",  # type: ignore[arg-type]
            rationale="Rationale",
            created_at=current_time,
        )


def test_pure_determinism_and_immutability(
    valid_proposal: ProductionChangeProposal,
    current_time: datetime,
) -> None:
    """INV-001, INV-006: Identical inputs yield identical hashes; models are frozen."""
    p1 = create_production_proposal(
        proposal_id="PROP-DET",
        hypothesis_id="HYP-DET",
        proposed_parameters={"a": 1, "b": 2},
        rationale="Deterministic test",
        created_at=current_time,
    )
    p2 = create_production_proposal(
        proposal_id="PROP-DET",
        hypothesis_id="HYP-DET",
        proposed_parameters={"b": 2, "a": 1},  # Key order difference
        rationale="Deterministic test",
        created_at=current_time,
    )

    assert p1.proposal_hash == p2.proposal_hash

    with pytest.raises(ValidationError):
        valid_proposal.is_approved = True  # type: ignore[misc]
