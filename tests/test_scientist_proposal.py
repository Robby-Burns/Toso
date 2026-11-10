"""Tests for Story SCI-007: Production Proposal & Human Approval Workflow.

Verifies:
- FR-017, FR-019: Controlled research proposals grounded in validated OOS evaluation.
- INV-014, INV-015: No autonomous production change; explicit cryptographic human approval required.
- INV-008: Fail-closed behavior on corrupt, missing, unverified, or UNKNOWN inputs.
- INV-001, INV-006: Pure determinism, immutable models, and cryptographic audit receipts.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.scientist import (
    BacktestResult,
    DegradationAnalysis,
    DuplicateProposalError,
    HumanApprovalToken,
    InvalidApprovalTokenError,
    InvalidProposalStateTransitionError,
    JournalSerializationError,
    OOSEvaluationResult,
    PerformanceMetrics,
    ProductionChangeProposal,
    ProposalNotFoundError,
    ProposalReceipt,
    ProposalRegistry,
    ProposalStatus,
    UnauthorizedProductionChangeError,
    UnknownScientistInputError,
    UnvalidatedProposalError,
    ValidationVerdict,
    adopt_proposal_to_config,
    compute_proposal_receipt_checksum,
    formulate_proposal_from_oos,
    generate_human_approval_signature,
    transition_proposal_status,
)


@pytest.fixture
def current_time() -> datetime:
    return datetime(2026, 9, 15, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def base_core_config(current_time: datetime) -> CoreConfig:
    return CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.APPROVED,
            approved_by="Owner-Alice",
            approved_at=current_time.isoformat(),
            description="Initial production config.",
        ),
        strategy=StrategyConfig(
            strategy_id="core_trend_v1",
            timeframe="1d",
            fast_ma_period=50,
            slow_ma_period=200,
            atr_period=14,
            breakout_lookback_bars=20,
            invalidation_atr_multiple=2.0,
        ),
        risk=RiskConfig(),
        capital=CapitalAllocationConfig(),
        universe=UniverseConfig(eligible_symbols=("BTC/USD", "ETH/USD")),
    )


@pytest.fixture
def mock_dummy_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        total_trades=10,
        winning_trades=7,
        losing_trades=3,
        win_rate=0.70,
        profit_factor=2.50,
        sharpe_ratio=1.80,
        max_drawdown=0.08,
        max_drawdown_dollars=8000.0,
        total_pnl=20000.0,
        net_return_pct=0.20,
    )


@pytest.fixture
def dummy_backtest_result(
    current_time: datetime, mock_dummy_metrics: PerformanceMetrics
) -> BacktestResult:
    return BacktestResult(
        backtest_id="BT-DUMMY-01",
        experiment_id="EXP-LOOKBACK-002",
        config_version="1.0.0",
        parameters={"breakout_lookback_bars": 25},
        metrics=mock_dummy_metrics,
        trades=(),
        equity_curve=(100_000.0, 120_000.0),
        start_date=current_time - timedelta(days=180),
        end_date=current_time,
        executed_at=current_time,
    )


@pytest.fixture
def validated_oos_result(
    current_time: datetime, dummy_backtest_result: BacktestResult
) -> OOSEvaluationResult:
    degradation = DegradationAnalysis(
        sharpe_retention_ratio=0.8889,
        profit_factor_retention_ratio=0.9091,
        drawdown_expansion_ratio=1.1250,
        net_return_retention_ratio=0.8000,
        verdict=ValidationVerdict.VALIDATED,
        notes="Held-out validation robust.",
    )
    return OOSEvaluationResult(
        evaluation_id="OOS-EVAL-EXP-LOOKBACK-002",
        experiment_id="EXP-LOOKBACK-002",
        hypothesis_id="HYP-BREAKOUT-002",
        in_sample_result=dummy_backtest_result,
        out_of_sample_result=dummy_backtest_result,
        degradation=degradation,
        verdict=ValidationVerdict.VALIDATED,
        is_approved_for_proposal=True,
        evaluated_at=current_time,
    )


@pytest.fixture
def valid_approval_token(
    current_time: datetime,
) -> tuple[str, HumanApprovalToken]:
    # Placeholder proposal_hash, overridden in tests where target proposal is known
    token_id = "TOKEN-2026-001"
    approver_id = "Owner-Alice"
    target_hash = "DUMMY_HASH"
    sig = generate_human_approval_signature(
        token_id=token_id,
        approver_id=approver_id,
        target_proposal_hash=target_hash,
        approved_at=current_time,
    )
    token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver_id,
        target_proposal_hash=target_hash,
        signature=sig,
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=24),
    )
    return target_hash, token


def test_ac001_evidence_grounded_proposal_formulation(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
    dummy_backtest_result: BacktestResult,
) -> None:
    """AC-001: Formulates proposal from validated OOS; rejects unvalidated/overfit results."""
    # Invalid input check
    with pytest.raises(UnknownScientistInputError):
        formulate_proposal_from_oos(
            proposal_id="",
            oos_result=validated_oos_result,
            proposed_parameters={"breakout_lookback_bars": 25},
            rationale="Test",
            created_at=current_time,
        )
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-LOOKBACK-001",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="25-bar lookback validated on held-out data with 88.9% Sharpe retention.",
        created_at=current_time,
    )

    assert proposal.proposal_id == "PROP-LOOKBACK-001"
    assert proposal.hypothesis_id == validated_oos_result.hypothesis_id
    assert proposal.status == ProposalStatus.DRAFT
    assert proposal.is_approved is False
    assert proposal.approval_token is None
    assert len(proposal.proposal_hash) == 64

    # Rejection of unvalidated OOS result
    overfit_degradation = DegradationAnalysis(
        sharpe_retention_ratio=0.30,
        profit_factor_retention_ratio=0.40,
        drawdown_expansion_ratio=2.50,
        net_return_retention_ratio=0.20,
        verdict=ValidationVerdict.OVERFIT,
        notes="Severe overfitting.",
    )
    overfit_oos_result = OOSEvaluationResult(
        evaluation_id="OOS-EVAL-OVERFIT-01",
        experiment_id="EXP-OVERFIT-01",
        hypothesis_id="HYP-01",
        in_sample_result=dummy_backtest_result,
        out_of_sample_result=dummy_backtest_result,
        degradation=overfit_degradation,
        verdict=ValidationVerdict.OVERFIT,
        is_approved_for_proposal=False,
        evaluated_at=current_time,
    )
    with pytest.raises(UnvalidatedProposalError, match="unvalidated OOS eval"):
        formulate_proposal_from_oos(
            proposal_id="PROP-BAD-01",
            oos_result=overfit_oos_result,
            proposed_parameters={"breakout_lookback_bars": 35},
            rationale="Trying anyway.",
            created_at=current_time,
        )


def test_ac002_cryptographic_proposal_hash_and_tamper_resistance(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-002: Proposal hash cryptographically seals parameters and rationale; alteration fails."""
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-LOOKBACK-002",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Evidence-backed rationale.",
        created_at=current_time,
    )

    # Valid token signed for original proposal hash
    token_id = "TOKEN-002"
    approver = "Owner-Alice"
    sig = generate_human_approval_signature(
        token_id, approver, proposal.proposal_hash, current_time
    )
    token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=proposal.proposal_hash,
        signature=sig,
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=2),
    )

    # Submitting for review then approving succeeds with matching hash
    submitted = transition_proposal_status(
        proposal, ProposalStatus.SUBMITTED_FOR_REVIEW, current_time
    )
    approved = transition_proposal_status(
        submitted, ProposalStatus.APPROVED, current_time, token=token
    )
    assert approved.is_approved is True

    # Tampered proposal with altered parameters and stale hash fails computed integrity check
    tampered_proposal = ProductionChangeProposal(
        proposal_id=proposal.proposal_id,
        hypothesis_id=proposal.hypothesis_id,
        proposed_parameters={"breakout_lookback_bars": 50},  # Tampered!
        rationale=proposal.rationale,
        proposal_hash=proposal.proposal_hash,  # Stale hash
        created_at=proposal.created_at,
        status=ProposalStatus.SUBMITTED_FOR_REVIEW,
    )
    with pytest.raises(
        InvalidApprovalTokenError, match="Proposal parameters or rationale have been tampered with"
    ):
        transition_proposal_status(
            tampered_proposal, ProposalStatus.APPROVED, current_time, token=token
        )


def test_ac003_proposal_lifecycle_state_machine(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-003: Strictly enforces legal lifecycle transitions and rejects stage-skipping."""
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-STATE-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Testing state machine.",
        created_at=current_time,
    )
    assert proposal.status == ProposalStatus.DRAFT

    # Illegal transition: DRAFT -> ADOPTED (stage skip)
    with pytest.raises(InvalidProposalStateTransitionError):
        transition_proposal_status(proposal, ProposalStatus.ADOPTED, current_time)

    # Legal: DRAFT -> SUBMITTED_FOR_REVIEW
    submitted = transition_proposal_status(
        proposal, ProposalStatus.SUBMITTED_FOR_REVIEW, current_time
    )
    assert submitted.status == ProposalStatus.SUBMITTED_FOR_REVIEW

    # Legal: SUBMITTED_FOR_REVIEW -> REJECTED
    rejected = transition_proposal_status(
        submitted, ProposalStatus.REJECTED, current_time, reason="Risk parameters unacceptable."
    )
    assert rejected.status == ProposalStatus.REJECTED
    assert rejected.rejection_reason == "Risk parameters unacceptable."

    # Legal: REJECTED -> DRAFT (returned for revision)
    revised = transition_proposal_status(rejected, ProposalStatus.DRAFT, current_time)
    assert revised.status == ProposalStatus.DRAFT

    # Legal: DRAFT -> WITHDRAWN (terminal)
    withdrawn = transition_proposal_status(revised, ProposalStatus.WITHDRAWN, current_time)
    assert withdrawn.status == ProposalStatus.WITHDRAWN

    # Terminal state rejection: WITHDRAWN -> DRAFT
    with pytest.raises(InvalidProposalStateTransitionError):
        transition_proposal_status(withdrawn, ProposalStatus.DRAFT, current_time)


def test_ac004_mandatory_human_approval_verification(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-004: Approving requires valid token; missing/expired/forged tokens fail closed."""
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-AUTH-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Testing token authorization.",
        created_at=current_time,
    )
    submitted = transition_proposal_status(
        proposal, ProposalStatus.SUBMITTED_FOR_REVIEW, current_time
    )

    # 1. Missing token raises InvalidApprovalTokenError
    with pytest.raises(InvalidApprovalTokenError, match="requires a valid HumanApprovalToken"):
        transition_proposal_status(submitted, ProposalStatus.APPROVED, current_time, token=None)

    # 2. Expired token raises InvalidApprovalTokenError
    token_id = "TOKEN-EXP"
    approver = "Owner-Alice"
    sig = generate_human_approval_signature(
        token_id, approver, proposal.proposal_hash, current_time
    )
    expired_token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=proposal.proposal_hash,
        signature=sig,
        approved_at=current_time - timedelta(hours=5),
        expires_at=current_time - timedelta(hours=1),  # Expired!
    )
    with pytest.raises(InvalidApprovalTokenError, match="Approval token expired"):
        transition_proposal_status(
            submitted, ProposalStatus.APPROVED, current_time, token=expired_token
        )

    # 3. Forged signature raises InvalidApprovalTokenError
    forged_token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=proposal.proposal_hash,
        signature="FORGED_SIGNATURE_HEX",
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=2),
    )
    with pytest.raises(InvalidApprovalTokenError, match="authenticity check failed"):
        transition_proposal_status(
            submitted, ProposalStatus.APPROVED, current_time, token=forged_token
        )


def test_ac005_atomic_production_config_adoption(
    validated_oos_result: OOSEvaluationResult,
    base_core_config: CoreConfig,
    current_time: datetime,
) -> None:
    """AC-005: Atomically adopts approved proposal into versioned CoreConfig contract."""
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-ADOPT-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Adopt 25-bar lookback.",
        created_at=current_time,
    )
    submitted = transition_proposal_status(
        proposal, ProposalStatus.SUBMITTED_FOR_REVIEW, current_time
    )

    token_id = "TOKEN-ADOPT-01"
    approver = "Owner-Alice"
    sig = generate_human_approval_signature(
        token_id, approver, proposal.proposal_hash, current_time
    )
    token = HumanApprovalToken(
        token_id=token_id,
        approver_id=approver,
        target_proposal_hash=proposal.proposal_hash,
        signature=sig,
        approved_at=current_time,
        expires_at=current_time + timedelta(hours=12),
    )

    approved = transition_proposal_status(
        submitted, ProposalStatus.APPROVED, current_time, token=token
    )

    adoption_time = current_time + timedelta(minutes=10)
    new_config, adopted_proposal, receipt = adopt_proposal_to_config(
        proposal=approved,
        current_config=base_core_config,
        new_version="1.1.0",
        adoption_time=adoption_time,
    )

    # Verify updated CoreConfig
    assert new_config.metadata.version == "1.1.0"
    assert new_config.metadata.status == ApprovalStatus.APPROVED
    assert new_config.metadata.approved_by == approver
    assert new_config.strategy.breakout_lookback_bars == 25  # Updated parameter!
    assert new_config.strategy.fast_ma_period == 50  # Preserved baseline parameter
    assert new_config.require_invalidation_before_entry is True

    # Verify adopted proposal status
    assert adopted_proposal.status == ProposalStatus.ADOPTED
    assert adopted_proposal.adopted_at == adoption_time

    # Verify receipt
    assert isinstance(receipt, ProposalReceipt)
    assert receipt.receipt_id == "RCPT-PROP-PROP-ADOPT-01-ADOPTED"
    assert receipt.new_status == ProposalStatus.ADOPTED
    assert len(receipt.checksum) == 64
    expected_checksum = compute_proposal_receipt_checksum(
        receipt_id=receipt.receipt_id,
        proposal_id=proposal.proposal_id,
        action="PROPOSAL_ADOPTED_TO_PRODUCTION",
        new_status=ProposalStatus.ADOPTED,
        timestamp=adoption_time,
    )
    assert receipt.checksum == expected_checksum


def test_ac006_unauthorized_production_change_rejected(
    validated_oos_result: OOSEvaluationResult,
    base_core_config: CoreConfig,
    current_time: datetime,
) -> None:
    """AC-006: Scientist layer cannot autonomously activate changes without approval (INV-014)."""
    # Unapproved proposal in DRAFT status
    draft_proposal = formulate_proposal_from_oos(
        proposal_id="PROP-UNAUTH-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 30},
        rationale="Attempting unapproved adoption.",
        created_at=current_time,
    )

    with pytest.raises(
        UnauthorizedProductionChangeError, match="requires verified APPROVED status"
    ):
        adopt_proposal_to_config(
            proposal=draft_proposal,
            current_config=base_core_config,
            new_version="2.0.0",
            adoption_time=current_time,
        )


def test_ac007_proposal_registry_lifecycle_and_receipts(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-007: ProposalRegistry registers, updates, and retrieves proposals issuing receipts."""
    registry = ProposalRegistry()

    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-REG-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Registration test.",
        created_at=current_time,
    )

    receipt = registry.register_proposal(proposal, current_time)
    assert isinstance(receipt, ProposalReceipt)
    assert receipt.action == "PROPOSAL_REGISTERED"
    assert receipt.proposal_id == "PROP-REG-01"

    # Retrieve proposal
    retrieved = registry.get_proposal("PROP-REG-01")
    assert retrieved.proposal_id == proposal.proposal_id

    # Update proposal
    submitted = transition_proposal_status(
        proposal, ProposalStatus.SUBMITTED_FOR_REVIEW, current_time
    )
    upd_receipt = registry.update_proposal(submitted, current_time)
    assert upd_receipt.action == "PROPOSAL_UPDATED"
    assert upd_receipt.previous_status == ProposalStatus.DRAFT
    assert upd_receipt.new_status == ProposalStatus.SUBMITTED_FOR_REVIEW


def test_ac008_duplicate_and_missing_proposal_errors(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-008: Duplicate IDs raise DuplicateProposalError; missing IDs raise not found error."""
    registry = ProposalRegistry()

    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-DUP-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Testing duplication.",
        created_at=current_time,
    )
    registry.register_proposal(proposal, current_time)

    # Duplicate registration
    with pytest.raises(DuplicateProposalError):
        registry.register_proposal(proposal, current_time)

    # Missing lookup
    with pytest.raises(ProposalNotFoundError):
        registry.get_proposal("NON-EXISTENT-PROP")

    # Missing update
    unrecorded = formulate_proposal_from_oos(
        proposal_id="PROP-UNRECORDED-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Not in registry.",
        created_at=current_time,
    )
    with pytest.raises(ProposalNotFoundError):
        registry.update_proposal(unrecorded, current_time)


def test_ac009_deterministic_querying_and_disk_persistence(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
    tmp_path: Path,
) -> None:
    """AC-009: Query filtering filters/sorts deterministically; JSONL persistence round-trips."""
    db_file = tmp_path / "proposals.jsonl"
    registry = ProposalRegistry(storage_path=db_file)

    p1 = formulate_proposal_from_oos(
        proposal_id="PROP-B",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="B",
        created_at=current_time,
    )
    p2 = formulate_proposal_from_oos(
        proposal_id="PROP-A",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 30},
        rationale="A",
        created_at=current_time,
    )

    registry.register_proposal(p1, current_time)
    registry.register_proposal(p2, current_time)

    # Query sorted deterministically by ID: PROP-A should be before PROP-B
    results = registry.query_proposals()
    assert len(results) == 2
    assert results[0].proposal_id == "PROP-A"
    assert results[1].proposal_id == "PROP-B"

    # Reload from disk into new registry
    reloaded = ProposalRegistry(storage_path=db_file)
    assert len(reloaded.query_proposals()) == 2
    assert reloaded.get_proposal("PROP-A").proposal_id == "PROP-A"

    # Corrupt line fails closed with JournalSerializationError
    with open(db_file, "a", encoding="utf-8") as f:
        f.write("{CORRUPT JSON LINE}\n")

    with pytest.raises(JournalSerializationError):
        ProposalRegistry(storage_path=db_file)


def test_ac010_pure_determinism_and_immutability(
    validated_oos_result: OOSEvaluationResult,
    current_time: datetime,
) -> None:
    """AC-010: Models are frozen and immutable (INV-001, INV-006)."""
    proposal = formulate_proposal_from_oos(
        proposal_id="PROP-FROZEN-01",
        oos_result=validated_oos_result,
        proposed_parameters={"breakout_lookback_bars": 25},
        rationale="Immutability check.",
        created_at=current_time,
    )

    with pytest.raises(ValidationError):
        proposal.proposal_id = "MUTATED"

    with pytest.raises(ValidationError):
        proposal.is_approved = True
