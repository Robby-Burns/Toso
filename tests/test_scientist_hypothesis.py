"""Tests for Story SCI-003: Hypothesis Definition & Storage.

Verifies:
- FR-017: Controlled research process and falsifiable hypothesis management.
- INV-014: Research hypotheses isolated from autonomous execution.
- INV-008: Fail-closed handling on corrupt, missing, or invalid inputs.
- INV-001, INV-006: Pure determinism and immutable audit receipts.
- Specification Section 23/24: Research quality, evidence grounding, and lifecycle transitions.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.scientist import (
    DuplicateHypothesisError,
    HypothesisNotFoundError,
    HypothesisReceipt,
    HypothesisStatus,
    HypothesisStorage,
    InvalidHypothesisStateTransitionError,
    ResearchHypothesis,
    UnknownScientistInputError,
    create_hypothesis,
    transition_hypothesis_status,
)


@pytest.fixture
def test_timestamp() -> datetime:
    return datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC)


@pytest.fixture
def sample_hypothesis(test_timestamp: datetime) -> ResearchHypothesis:
    return create_hypothesis(
        hypothesis_id="HYP-BREAKOUT-001",
        title="25-bar Breakout Noise Dampening",
        statement=(
            "Increasing breakout_lookback_bars from 20 to 25 will reduce false breakout "
            "entries in choppy regimes by at least 15% without degrading bull trend capture."
        ),
        rationale="Observations in journal indicate cluster of invalidations within 3 bars.",
        expected_direction="Lower false entry rate, higher overall profit factor.",
        created_at=test_timestamp,
        target_parameters=("breakout_lookback_bars",),
    )


def test_ac001_hypothesis_creation_and_fields(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """AC-001: Creates immutable, falsifiable hypothesis with grounding and initial DRAFT state."""
    assert sample_hypothesis.hypothesis_id == "HYP-BREAKOUT-001"
    assert sample_hypothesis.title == "25-bar Breakout Noise Dampening"
    assert "breakout_lookback_bars" in sample_hypothesis.target_parameters
    assert sample_hypothesis.status == HypothesisStatus.DRAFT
    assert sample_hypothesis.created_at == test_timestamp
    assert sample_hypothesis.updated_at == test_timestamp
    assert len(sample_hypothesis.status_history) == 1
    assert sample_hypothesis.status_history[0].status == HypothesisStatus.DRAFT


def test_ac002_legal_state_transitions(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """AC-002: Executes valid lifecycle state transitions (DRAFT -> TESTING -> SUPPORTED)."""
    t1 = test_timestamp + timedelta(hours=1)
    hyp_testing = transition_hypothesis_status(
        hypothesis=sample_hypothesis,
        new_status=HypothesisStatus.TESTING,
        timestamp=t1,
        reason="Beginning backtest suite execution in SCI-005.",
    )
    assert hyp_testing.status == HypothesisStatus.TESTING
    assert hyp_testing.updated_at == t1
    assert len(hyp_testing.status_history) == 2

    t2 = t1 + timedelta(days=1)
    hyp_supported = transition_hypothesis_status(
        hypothesis=hyp_testing,
        new_status=HypothesisStatus.SUPPORTED,
        timestamp=t2,
        reason="Backtest confirmed 18% reduction in false entries.",
        evidence_summary="Sharpe improved from 1.42 to 1.65; drawdowns reduced by 2.1%.",
    )
    assert hyp_supported.status == HypothesisStatus.SUPPORTED
    assert hyp_supported.updated_at == t2
    assert len(hyp_supported.status_history) == 3
    assert hyp_supported.evidence_summary is not None
    assert "Sharpe improved" in hyp_supported.evidence_summary


def test_ac003_illegal_state_transitions_rejected(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """AC-003: Disallows skipping stages (e.g. DRAFT -> SUPPORTED) or resurrecting ABANDONED."""
    t1 = test_timestamp + timedelta(hours=1)

    # Cannot skip directly from DRAFT to SUPPORTED (must test first)
    with pytest.raises(InvalidHypothesisStateTransitionError) as exc_info:
        transition_hypothesis_status(
            hypothesis=sample_hypothesis,
            new_status=HypothesisStatus.SUPPORTED,
            timestamp=t1,
        )
    assert "Illegal state transition" in str(exc_info.value)

    # Abandoning a hypothesis
    hyp_abandoned = transition_hypothesis_status(
        hypothesis=sample_hypothesis,
        new_status=HypothesisStatus.ABANDONED,
        timestamp=t1,
        reason="No longer relevant due to regime shift.",
    )
    assert hyp_abandoned.status == HypothesisStatus.ABANDONED

    # Terminal state: cannot transition out of ABANDONED
    t2 = t1 + timedelta(hours=1)
    with pytest.raises(InvalidHypothesisStateTransitionError) as exc_info:
        transition_hypothesis_status(
            hypothesis=hyp_abandoned,
            new_status=HypothesisStatus.TESTING,
            timestamp=t2,
        )
    assert "Illegal state transition" in str(exc_info.value)


def test_ac004_hypothesis_storage_lifecycle(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """AC-004: HypothesisStorage accurately stores, updates, and issues receipts."""
    storage = HypothesisStorage()

    # 1. Add
    receipt_add = storage.add_hypothesis(sample_hypothesis, test_timestamp)
    assert isinstance(receipt_add, HypothesisReceipt)
    assert receipt_add.action == "CREATED"
    assert receipt_add.new_status == HypothesisStatus.DRAFT
    assert storage.total_count == 1

    # 2. Get
    retrieved = storage.get_hypothesis("HYP-BREAKOUT-001")
    assert retrieved == sample_hypothesis

    # 3. Update
    t1 = test_timestamp + timedelta(hours=2)
    updated_hyp = transition_hypothesis_status(
        hypothesis=retrieved,
        new_status=HypothesisStatus.TESTING,
        timestamp=t1,
        reason="Testing initiated.",
    )
    receipt_update = storage.update_hypothesis(updated_hyp, t1)
    assert receipt_update.action == "UPDATED"
    assert receipt_update.previous_status == HypothesisStatus.DRAFT
    assert receipt_update.new_status == HypothesisStatus.TESTING

    retrieved2 = storage.get_hypothesis("HYP-BREAKOUT-001")
    assert retrieved2.status == HypothesisStatus.TESTING


def test_ac005_query_and_filter_hypotheses(test_timestamp: datetime) -> None:
    """AC-005: Querying supports filtering by status and parameter with deterministic sort."""
    storage = HypothesisStorage()

    h1 = create_hypothesis(
        hypothesis_id="HYP-001",
        title="ATR multiple tweak",
        statement="Increasing ATR multiple increases win rate.",
        rationale="Preliminary data.",
        expected_direction="Higher win rate",
        created_at=test_timestamp,
        target_parameters=("invalidation_atr_multiple",),
        initial_status=HypothesisStatus.DRAFT,
    )
    h2 = create_hypothesis(
        hypothesis_id="HYP-002",
        title="Lookback bar expansion",
        statement="25-bar lookback reduces whipsaw.",
        rationale="Noise analysis.",
        expected_direction="Fewer false signals",
        created_at=test_timestamp,
        target_parameters=("breakout_lookback_bars",),
        initial_status=HypothesisStatus.DRAFT,
    )

    t1 = test_timestamp + timedelta(minutes=10)
    h2_testing = transition_hypothesis_status(h2, HypothesisStatus.TESTING, t1)

    storage.add_hypothesis(h1, test_timestamp)
    storage.add_hypothesis(h2_testing, t1)

    # Filter by status
    drafts = storage.list_hypotheses(status=HypothesisStatus.DRAFT)
    assert len(drafts) == 1
    assert drafts[0].hypothesis_id == "HYP-001"

    testings = storage.list_hypotheses(status=HypothesisStatus.TESTING)
    assert len(testings) == 1
    assert testings[0].hypothesis_id == "HYP-002"

    # Filter by target_parameter
    by_param = storage.list_hypotheses(target_parameter="breakout_lookback_bars")
    assert len(by_param) == 1
    assert by_param[0].hypothesis_id == "HYP-002"

    # List all
    all_hyps = storage.list_hypotheses()
    assert len(all_hyps) == 2
    assert all_hyps[0].hypothesis_id == "HYP-001"
    assert all_hyps[1].hypothesis_id == "HYP-002"


def test_ac006_duplicate_and_missing_hypothesis_errors(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """AC-006: Duplicate addition raises DuplicateHypothesisError; missing ID raises not found."""
    storage = HypothesisStorage()
    storage.add_hypothesis(sample_hypothesis, test_timestamp)

    # Duplicate add
    with pytest.raises(DuplicateHypothesisError) as exc_info:
        storage.add_hypothesis(sample_hypothesis, test_timestamp)
    assert "already exists in storage" in str(exc_info.value)

    # Missing lookup
    with pytest.raises(HypothesisNotFoundError) as exc_info:
        storage.get_hypothesis("NON-EXISTENT-ID")
    assert "not found in storage" in str(exc_info.value)


def test_ac007_persistent_disk_storage(
    sample_hypothesis: ResearchHypothesis, tmp_path: Path, test_timestamp: datetime
) -> None:
    """AC-007: Hypotheses write and reload deterministically from persistent JSONL file."""
    disk_file = tmp_path / "hypotheses.jsonl"
    storage1 = HypothesisStorage(persistence_path=disk_file)

    storage1.add_hypothesis(sample_hypothesis, test_timestamp)
    assert disk_file.exists()

    # Re-instantiate storage from disk
    storage2 = HypothesisStorage(persistence_path=disk_file)
    assert storage2.total_count == 1
    loaded = storage2.get_hypothesis(sample_hypothesis.hypothesis_id)
    assert loaded == sample_hypothesis


def test_fail_closed_on_corrupt_unknown_inputs(
    sample_hypothesis: ResearchHypothesis, test_timestamp: datetime
) -> None:
    """INV-008: Fail-closed on empty strings, non-chronological timestamps, and corrupt payloads."""
    # Empty hypothesis_id or whitespace
    for bad_id in ["", "   ", None]:
        with pytest.raises(UnknownScientistInputError):
            create_hypothesis(
                hypothesis_id=bad_id,  # type: ignore[arg-type]
                title="Title",
                statement="Statement",
                rationale="Rationale",
                expected_direction="Direction",
                created_at=test_timestamp,
            )

    # Empty statement or title
    with pytest.raises(UnknownScientistInputError):
        create_hypothesis(
            hypothesis_id="HYP-01",
            title="Title",
            statement="   ",
            rationale="Rationale",
            expected_direction="Direction",
            created_at=test_timestamp,
        )

    # Non-chronological transition timestamp
    past_timestamp = test_timestamp - timedelta(minutes=5)
    with pytest.raises(UnknownScientistInputError):
        transition_hypothesis_status(
            hypothesis=sample_hypothesis,
            new_status=HypothesisStatus.TESTING,
            timestamp=past_timestamp,
        )


def test_pure_determinism_and_immutability(
    sample_hypothesis: ResearchHypothesis,
) -> None:
    """INV-001, INV-006: ResearchHypothesis model is strictly frozen and extra-forbidden."""
    with pytest.raises(ValidationError):
        sample_hypothesis.title = "Altered Title"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        sample_hypothesis.status = HypothesisStatus.SUPPORTED  # type: ignore[misc]
