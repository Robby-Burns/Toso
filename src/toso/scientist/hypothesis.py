"""TOSO Scientist Hypothesis Definition & Storage Engine.

Implements FR-017, INV-014, INV-008, INV-001, INV-006:
- Defines structured, falsifiable research hypotheses grounded in empirical observations.
- Manages strictly validated lifecycle state transitions (DRAFT -> TESTING -> SUPPORTED/REFUTED).
- Provides deterministic storage, retrieval, and querying (by status, parameter, or id).
- Supports optional JSONL disk persistence with integrity verification.
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
- Fail-closed handling for UNKNOWN, corrupted, or invalid inputs (INV-008).
"""

import json
from datetime import datetime
from pathlib import Path

from toso.scientist.errors import (
    DuplicateHypothesisError,
    HypothesisNotFoundError,
    InvalidHypothesisStateTransitionError,
    JournalSerializationError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    HypothesisReceipt,
    HypothesisStatus,
    HypothesisStatusEntry,
    ResearchHypothesis,
)

# Canonical legal lifecycle transitions for scientific research
LEGAL_TRANSITIONS: dict[HypothesisStatus, set[HypothesisStatus]] = {
    HypothesisStatus.DRAFT: {HypothesisStatus.TESTING, HypothesisStatus.ABANDONED},
    HypothesisStatus.TESTING: {
        HypothesisStatus.SUPPORTED,
        HypothesisStatus.REFUTED,
        HypothesisStatus.ABANDONED,
    },
    HypothesisStatus.SUPPORTED: {HypothesisStatus.TESTING, HypothesisStatus.ABANDONED},
    HypothesisStatus.REFUTED: {HypothesisStatus.TESTING, HypothesisStatus.ABANDONED},
    HypothesisStatus.ABANDONED: set(),  # Terminal state
}


def _validate_non_empty_str(val: str, field_name: str) -> str:
    """Validate that a string field is non-empty and non-whitespace."""
    if not isinstance(val, str) or not val.strip():
        raise UnknownScientistInputError(
            f"Field '{field_name}' must be a non-empty string, got {val!r}"
        )
    return val.strip()


def create_hypothesis(
    hypothesis_id: str,
    title: str,
    statement: str,
    rationale: str,
    expected_direction: str,
    created_at: datetime,
    target_parameters: tuple[str, ...] | list[str] = (),
    initial_status: HypothesisStatus = HypothesisStatus.DRAFT,
) -> ResearchHypothesis:
    """Factory creating an immutable, validated ResearchHypothesis (FR-017).

    Args:
        hypothesis_id: Deterministic unique identifier.
        title: Descriptive hypothesis title.
        statement: Falsifiable proposition describing the expected effect.
        rationale: Analytical and observational justification.
        expected_direction: Predicted directional outcome.
        created_at: Echoed creation timestamp anchor.
        target_parameters: Optional strategy parameters under investigation.
        initial_status: Starting lifecycle state (default: DRAFT).

    Returns:
        Immutable ResearchHypothesis instance.

    Raises:
        UnknownScientistInputError: On empty or invalid inputs.
    """
    clean_id = _validate_non_empty_str(hypothesis_id, "hypothesis_id")
    clean_title = _validate_non_empty_str(title, "title")
    clean_stmt = _validate_non_empty_str(statement, "statement")
    clean_rat = _validate_non_empty_str(rationale, "rationale")
    clean_dir = _validate_non_empty_str(expected_direction, "expected_direction")

    if not isinstance(created_at, datetime):
        raise UnknownScientistInputError(f"created_at must be a datetime, got {type(created_at)}")
    if not isinstance(initial_status, HypothesisStatus):
        raise UnknownScientistInputError(
            f"initial_status must be a HypothesisStatus, got {type(initial_status)}"
        )

    clean_params: list[str] = []
    for p in target_parameters:
        clean_params.append(_validate_non_empty_str(p, "target_parameter"))

    init_history = (
        HypothesisStatusEntry(
            status=initial_status,
            timestamp=created_at,
            reason="Initial hypothesis formulation.",
        ),
    )

    return ResearchHypothesis(
        hypothesis_id=clean_id,
        title=clean_title,
        statement=clean_stmt,
        rationale=clean_rat,
        target_parameters=tuple(clean_params),
        expected_direction=clean_dir,
        status=initial_status,
        created_at=created_at,
        updated_at=created_at,
        status_history=init_history,
        evidence_summary=None,
    )


def transition_hypothesis_status(
    hypothesis: ResearchHypothesis,
    new_status: HypothesisStatus,
    timestamp: datetime,
    reason: str = "",
    evidence_summary: str | None = None,
) -> ResearchHypothesis:
    """Pure function transitioning a ResearchHypothesis to a new lifecycle status.

    Args:
        hypothesis: Existing ResearchHypothesis to transition.
        new_status: Desired target HypothesisStatus.
        timestamp: Transition timestamp anchor.
        reason: Audit rationale for status transition.
        evidence_summary: Optional summary of experimental or backtest findings.

    Returns:
        New immutable ResearchHypothesis with updated status and transition history.

    Raises:
        InvalidHypothesisStateTransitionError: If the transition is illegal.
        UnknownScientistInputError: If timestamp is non-chronological or invalid.
    """
    if not isinstance(hypothesis, ResearchHypothesis):
        raise UnknownScientistInputError(f"Invalid hypothesis type: {type(hypothesis)}")
    if not isinstance(new_status, HypothesisStatus):
        raise UnknownScientistInputError(f"Invalid new_status type: {type(new_status)}")
    if not isinstance(timestamp, datetime):
        raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

    if timestamp < hypothesis.updated_at:
        raise UnknownScientistInputError(
            f"Transition timestamp ({timestamp}) cannot precede last update "
            f"({hypothesis.updated_at})."
        )

    allowed = LEGAL_TRANSITIONS.get(hypothesis.status, set())
    if new_status not in allowed:
        raise InvalidHypothesisStateTransitionError(
            f"Illegal state transition from {hypothesis.status.value} to {new_status.value} "
            f"for hypothesis '{hypothesis.hypothesis_id}'."
        )

    new_history_entry = HypothesisStatusEntry(
        status=new_status,
        timestamp=timestamp,
        reason=reason or f"Transitioned to {new_status.value}.",
    )
    updated_history = hypothesis.status_history + (new_history_entry,)

    new_evidence = evidence_summary if evidence_summary is not None else hypothesis.evidence_summary

    return ResearchHypothesis(
        hypothesis_id=hypothesis.hypothesis_id,
        title=hypothesis.title,
        statement=hypothesis.statement,
        rationale=hypothesis.rationale,
        target_parameters=hypothesis.target_parameters,
        expected_direction=hypothesis.expected_direction,
        status=new_status,
        created_at=hypothesis.created_at,
        updated_at=timestamp,
        status_history=updated_history,
        evidence_summary=new_evidence,
    )


class HypothesisStorage:
    """Deterministic repository for storing and querying scientific hypotheses (FR-017)."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        """Initialize the storage with optional JSONL disk persistence."""
        self._hypotheses: dict[str, ResearchHypothesis] = {}
        self._persistence_path: Path | None = (
            Path(persistence_path).resolve() if persistence_path is not None else None
        )
        if self._persistence_path is not None and self._persistence_path.exists():
            self._load_from_disk()

    @property
    def total_count(self) -> int:
        """Count of stored hypotheses."""
        return len(self._hypotheses)

    def add_hypothesis(
        self, hypothesis: ResearchHypothesis, timestamp: datetime
    ) -> HypothesisReceipt:
        """Store a newly formulated hypothesis.

        Args:
            hypothesis: The ResearchHypothesis to store.
            timestamp: Echoed storage timestamp.

        Returns:
            HypothesisReceipt confirming storage.

        Raises:
            DuplicateHypothesisError: If the hypothesis ID is already registered.
            UnknownScientistInputError: If inputs are invalid.
        """
        if not isinstance(hypothesis, ResearchHypothesis):
            raise UnknownScientistInputError(f"Invalid hypothesis type: {type(hypothesis)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        if hypothesis.hypothesis_id in self._hypotheses:
            raise DuplicateHypothesisError(
                f"Hypothesis with ID '{hypothesis.hypothesis_id}' already exists in storage."
            )

        self._hypotheses[hypothesis.hypothesis_id] = hypothesis

        if self._persistence_path is not None:
            self._save_to_disk()

        receipt_id = f"HYP-RCPT-{hypothesis.hypothesis_id}-{int(timestamp.timestamp())}"
        return HypothesisReceipt(
            receipt_id=receipt_id,
            hypothesis_id=hypothesis.hypothesis_id,
            action="CREATED",
            previous_status=None,
            new_status=hypothesis.status,
            timestamp=timestamp,
            details=(
                f"Hypothesis '{hypothesis.title}' stored with status {hypothesis.status.value}."
            ),
        )

    def get_hypothesis(self, hypothesis_id: str) -> ResearchHypothesis:
        """Retrieve a stored hypothesis by its deterministic ID.

        Args:
            hypothesis_id: Unique hypothesis identifier.

        Returns:
            The immutable ResearchHypothesis.

        Raises:
            HypothesisNotFoundError: If no hypothesis with this ID exists.
        """
        clean_id = _validate_non_empty_str(hypothesis_id, "hypothesis_id")
        hyp = self._hypotheses.get(clean_id)
        if hyp is None:
            raise HypothesisNotFoundError(f"Hypothesis with ID '{clean_id}' not found in storage.")
        return hyp

    def update_hypothesis(
        self, hypothesis: ResearchHypothesis, timestamp: datetime
    ) -> HypothesisReceipt:
        """Update an existing stored hypothesis (e.g. after status transition).

        Args:
            hypothesis: The updated ResearchHypothesis.
            timestamp: Echoed update timestamp.

        Returns:
            HypothesisReceipt documenting the update.

        Raises:
            HypothesisNotFoundError: If hypothesis does not exist in storage.
        """
        if not isinstance(hypothesis, ResearchHypothesis):
            raise UnknownScientistInputError(f"Invalid hypothesis type: {type(hypothesis)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        existing = self.get_hypothesis(hypothesis.hypothesis_id)
        prev_status = existing.status
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis

        if self._persistence_path is not None:
            self._save_to_disk()

        receipt_id = f"HYP-RCPT-{hypothesis.hypothesis_id}-{int(timestamp.timestamp())}"
        return HypothesisReceipt(
            receipt_id=receipt_id,
            hypothesis_id=hypothesis.hypothesis_id,
            action="UPDATED",
            previous_status=prev_status,
            new_status=hypothesis.status,
            timestamp=timestamp,
            details=(
                f"Hypothesis '{hypothesis.hypothesis_id}' updated from "
                f"{prev_status.value} to {hypothesis.status.value}."
            ),
        )

    def list_hypotheses(
        self,
        status: HypothesisStatus | None = None,
        target_parameter: str | None = None,
    ) -> tuple[ResearchHypothesis, ...]:
        """Query and filter hypotheses deterministically.

        Args:
            status: Optional status filter.
            target_parameter: Optional target parameter filter.

        Returns:
            Deterministic tuple of matching hypotheses sorted by hypothesis_id.
        """
        results: list[ResearchHypothesis] = []
        clean_param = target_parameter.strip() if target_parameter else None

        for hyp in self._hypotheses.values():
            if status is not None and hyp.status != status:
                continue
            if clean_param is not None and clean_param not in hyp.target_parameters:
                continue
            results.append(hyp)

        results.sort(key=lambda h: h.hypothesis_id)
        return tuple(results)

    def _save_to_disk(self) -> None:
        """Write all hypotheses to persistent JSONL file."""
        if self._persistence_path is None:
            return
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        # Sort by hypothesis_id for deterministic disk serialization
        sorted_hyps = sorted(self._hypotheses.values(), key=lambda h: h.hypothesis_id)
        with open(self._persistence_path, "w", encoding="utf-8") as f:
            for h in sorted_hyps:
                f.write(h.model_dump_json() + "\n")

    def _load_from_disk(self) -> None:
        """Load hypotheses from persistent JSONL file."""
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        with open(self._persistence_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean:
                    continue
                try:
                    data = json.loads(clean)
                    hyp = ResearchHypothesis.model_validate(data)
                    self._hypotheses[hyp.hypothesis_id] = hyp
                except Exception as e:
                    raise JournalSerializationError(
                        f"Failed parsing hypothesis line {line_no}: {e}"
                    ) from e
