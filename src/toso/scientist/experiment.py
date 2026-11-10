"""TOSO Scientist Experiment Design & Parameterization Engine.

Implements FR-017, FR-018, INV-014, INV-008, INV-001, INV-006:
- Designs structured, reproducible backtest experiments linked to hypotheses (FR-017).
- Enforces strict temporal data separation between in-sample and out-of-sample datasets (FR-018).
- Disallows data leakage or overlapping development/validation intervals (DataLeakageError).
- Provides deterministic experiment registry, state management, and filtered queries.
- Supports optional JSONL disk persistence with integrity verification.
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
- Fail-closed handling for UNKNOWN, corrupted, or invalid inputs (INV-008).
"""

import json
from datetime import datetime
from pathlib import Path

from toso.scientist.errors import (
    DataLeakageError,
    DuplicateExperimentError,
    ExperimentNotFoundError,
    InvalidExperimentDesignError,
    InvalidExperimentStateTransitionError,
    JournalSerializationError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    DatasetSplit,
    ExperimentDesign,
    ExperimentReceipt,
    ExperimentStatus,
    ParameterVariation,
)

# Canonical legal lifecycle transitions for research experiments
LEGAL_EXPERIMENT_TRANSITIONS: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.DRAFT: {ExperimentStatus.CONFIGURED, ExperimentStatus.CANCELLED},
    ExperimentStatus.CONFIGURED: {ExperimentStatus.RUNNING, ExperimentStatus.CANCELLED},
    ExperimentStatus.RUNNING: {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.COMPLETED: set(),  # Terminal state
    ExperimentStatus.FAILED: {ExperimentStatus.CONFIGURED, ExperimentStatus.CANCELLED},
    ExperimentStatus.CANCELLED: set(),  # Terminal state
}


def _validate_non_empty_str(val: str, field_name: str) -> str:
    """Validate that a string field is non-empty and non-whitespace."""
    if not isinstance(val, str) or not val.strip():
        raise UnknownScientistInputError(
            f"Field '{field_name}' must be a non-empty string, got {val!r}"
        )
    return val.strip()


def validate_dataset_split(split: DatasetSplit) -> None:
    """Validate strict temporal data separation between in-sample and out-of-sample data (FR-018).

    Enforces:
    1. start_date < in_sample_end_date (valid discovery period).
    2. in_sample_end_date < out_of_sample_end_date (strict chronological validation holdout).

    Raises:
        DataLeakageError: If dates overlap or violate holdout separation (FR-018).
        UnknownScientistInputError: If split format or symbols are invalid.
    """
    if not isinstance(split, DatasetSplit):
        raise UnknownScientistInputError(f"split must be a DatasetSplit, got {type(split)}")

    if split.start_date >= split.in_sample_end_date:
        raise DataLeakageError(
            f"Invalid split: start_date ({split.start_date}) must precede "
            f"in_sample_end_date ({split.in_sample_end_date})."
        )

    if split.in_sample_end_date >= split.out_of_sample_end_date:
        raise DataLeakageError(
            f"Data leakage breach: in_sample_end_date ({split.in_sample_end_date}) "
            f"must strictly precede out_of_sample_end_date ({split.out_of_sample_end_date}). "
            "Discovery and held-out validation data must be strictly separated (FR-018)."
        )

    if not split.symbols:
        raise InvalidExperimentDesignError("DatasetSplit must contain at least one symbol.")

    for s in split.symbols:
        _validate_non_empty_str(s, "symbol")


def create_experiment_design(
    experiment_id: str,
    hypothesis_id: str,
    title: str,
    description: str,
    baseline_config_version: str,
    parameter_variations: tuple[ParameterVariation, ...] | list[ParameterVariation],
    dataset_split: DatasetSplit,
    target_metrics: tuple[str, ...] | list[str],
    created_at: datetime,
    initial_status: ExperimentStatus = ExperimentStatus.DRAFT,
    notes: str = "",
) -> ExperimentDesign:
    """Factory creating an immutable, validated ExperimentDesign (FR-017, FR-018).

    Args:
        experiment_id: Unique deterministic experiment ID.
        hypothesis_id: Linked hypothesis ID.
        title: Short title of the experiment.
        description: Methodological explanation.
        baseline_config_version: Baseline version string (e.g. '1.0.0').
        parameter_variations: Variations tested against baseline.
        dataset_split: Validated train/test dataset partition.
        target_metrics: Metrics evaluated (e.g. Sharpe, drawdown).
        created_at: Formulation timestamp anchor.
        initial_status: Initial status (default: DRAFT).
        notes: Supplementary notes.

    Returns:
        Immutable ExperimentDesign instance.

    Raises:
        DataLeakageError: If dataset split violates separation.
        InvalidExperimentDesignError: If variations or metrics are missing.
        UnknownScientistInputError: On empty or invalid inputs.
    """
    clean_id = _validate_non_empty_str(experiment_id, "experiment_id")
    clean_hyp_id = _validate_non_empty_str(hypothesis_id, "hypothesis_id")
    clean_title = _validate_non_empty_str(title, "title")
    clean_desc = _validate_non_empty_str(description, "description")
    clean_ver = _validate_non_empty_str(baseline_config_version, "baseline_config_version")

    if not isinstance(created_at, datetime):
        raise UnknownScientistInputError(f"created_at must be a datetime, got {type(created_at)}")
    if not isinstance(initial_status, ExperimentStatus):
        raise UnknownScientistInputError(
            f"initial_status must be an ExperimentStatus, got {type(initial_status)}"
        )

    validate_dataset_split(dataset_split)

    if not parameter_variations:
        raise InvalidExperimentDesignError(
            "Experiment must specify at least one parameter variation."
        )
    for v in parameter_variations:
        if not isinstance(v, ParameterVariation):
            raise InvalidExperimentDesignError(
                f"Item in parameter_variations must be ParameterVariation, got {type(v)}"
            )
        _validate_non_empty_str(v.parameter_name, "parameter_name")

    if not target_metrics:
        raise InvalidExperimentDesignError("Experiment must specify at least one target metric.")
    clean_metrics: list[str] = []
    for m in target_metrics:
        clean_metrics.append(_validate_non_empty_str(m, "target_metric"))

    return ExperimentDesign(
        experiment_id=clean_id,
        hypothesis_id=clean_hyp_id,
        title=clean_title,
        description=clean_desc,
        baseline_config_version=clean_ver,
        parameter_variations=tuple(parameter_variations),
        dataset_split=dataset_split,
        target_metrics=tuple(clean_metrics),
        status=initial_status,
        created_at=created_at,
        updated_at=created_at,
        notes=notes,
    )


def transition_experiment_status(
    experiment: ExperimentDesign,
    new_status: ExperimentStatus,
    timestamp: datetime,
    notes: str = "",
) -> ExperimentDesign:
    """Pure function transitioning an ExperimentDesign to a new lifecycle status.

    Args:
        experiment: Existing ExperimentDesign to transition.
        new_status: Desired target ExperimentStatus.
        timestamp: Transition timestamp anchor.
        notes: Audit notes for the status change.

    Returns:
        New immutable ExperimentDesign with updated status and timestamp.

    Raises:
        InvalidExperimentStateTransitionError: If the transition is illegal.
        UnknownScientistInputError: If timestamp is non-chronological or invalid.
    """
    if not isinstance(experiment, ExperimentDesign):
        raise UnknownScientistInputError(f"Invalid experiment type: {type(experiment)}")
    if not isinstance(new_status, ExperimentStatus):
        raise UnknownScientistInputError(f"Invalid new_status type: {type(new_status)}")
    if not isinstance(timestamp, datetime):
        raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

    if timestamp < experiment.updated_at:
        raise UnknownScientistInputError(
            f"Transition timestamp ({timestamp}) cannot precede last update "
            f"({experiment.updated_at})."
        )

    allowed = LEGAL_EXPERIMENT_TRANSITIONS.get(experiment.status, set())
    if new_status not in allowed:
        raise InvalidExperimentStateTransitionError(
            f"Illegal state transition from {experiment.status.value} to {new_status.value} "
            f"for experiment '{experiment.experiment_id}'."
        )

    updated_notes = f"{experiment.notes}\n{notes}".strip() if notes else experiment.notes

    return ExperimentDesign(
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        title=experiment.title,
        description=experiment.description,
        baseline_config_version=experiment.baseline_config_version,
        parameter_variations=experiment.parameter_variations,
        dataset_split=experiment.dataset_split,
        target_metrics=experiment.target_metrics,
        status=new_status,
        created_at=experiment.created_at,
        updated_at=timestamp,
        notes=updated_notes,
    )


class ExperimentRegistry:
    """Deterministic repository for storing and managing backtest experiments (FR-017)."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        """Initialize the experiment registry with optional JSONL disk persistence."""
        self._experiments: dict[str, ExperimentDesign] = {}
        self._persistence_path: Path | None = (
            Path(persistence_path).resolve() if persistence_path is not None else None
        )
        if self._persistence_path is not None and self._persistence_path.exists():
            self._load_from_disk()

    @property
    def total_count(self) -> int:
        """Count of registered experiments."""
        return len(self._experiments)

    def register_experiment(
        self, design: ExperimentDesign, timestamp: datetime
    ) -> ExperimentReceipt:
        """Register a newly designed experiment.

        Args:
            design: The ExperimentDesign to register.
            timestamp: Echoed registration timestamp.

        Returns:
            ExperimentReceipt confirming registration.

        Raises:
            DuplicateExperimentError: If the experiment ID already exists.
            UnknownScientistInputError: If inputs are invalid.
        """
        if not isinstance(design, ExperimentDesign):
            raise UnknownScientistInputError(f"Invalid design type: {type(design)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        if design.experiment_id in self._experiments:
            raise DuplicateExperimentError(
                f"Experiment with ID '{design.experiment_id}' already registered."
            )

        self._experiments[design.experiment_id] = design

        if self._persistence_path is not None:
            self._save_to_disk()

        receipt_id = f"EXP-RCPT-{design.experiment_id}-{int(timestamp.timestamp())}"
        return ExperimentReceipt(
            receipt_id=receipt_id,
            experiment_id=design.experiment_id,
            hypothesis_id=design.hypothesis_id,
            action="REGISTERED",
            previous_status=None,
            new_status=design.status,
            timestamp=timestamp,
            details=f"Experiment '{design.title}' registered with status {design.status.value}.",
        )

    def get_experiment(self, experiment_id: str) -> ExperimentDesign:
        """Retrieve an experiment by its deterministic identifier.

        Args:
            experiment_id: Unique experiment identifier.

        Returns:
            The immutable ExperimentDesign.

        Raises:
            ExperimentNotFoundError: If no experiment with this ID exists.
        """
        clean_id = _validate_non_empty_str(experiment_id, "experiment_id")
        exp = self._experiments.get(clean_id)
        if exp is None:
            raise ExperimentNotFoundError(f"Experiment with ID '{clean_id}' not found in registry.")
        return exp

    def update_experiment(self, design: ExperimentDesign, timestamp: datetime) -> ExperimentReceipt:
        """Update an existing experiment in the registry (e.g. after status transition).

        Args:
            design: Updated ExperimentDesign.
            timestamp: Echoed update timestamp.

        Returns:
            ExperimentReceipt documenting the update.

        Raises:
            ExperimentNotFoundError: If experiment does not exist in registry.
        """
        if not isinstance(design, ExperimentDesign):
            raise UnknownScientistInputError(f"Invalid design type: {type(design)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        existing = self.get_experiment(design.experiment_id)
        prev_status = existing.status
        self._experiments[design.experiment_id] = design

        if self._persistence_path is not None:
            self._save_to_disk()

        receipt_id = f"EXP-RCPT-{design.experiment_id}-{int(timestamp.timestamp())}"
        return ExperimentReceipt(
            receipt_id=receipt_id,
            experiment_id=design.experiment_id,
            hypothesis_id=design.hypothesis_id,
            action="UPDATED",
            previous_status=prev_status,
            new_status=design.status,
            timestamp=timestamp,
            details=(
                f"Experiment '{design.experiment_id}' updated from "
                f"{prev_status.value} to {design.status.value}."
            ),
        )

    def list_experiments(
        self,
        status: ExperimentStatus | None = None,
        hypothesis_id: str | None = None,
    ) -> tuple[ExperimentDesign, ...]:
        """Query and filter experiments deterministically.

        Args:
            status: Optional status filter.
            hypothesis_id: Optional linked hypothesis filter.

        Returns:
            Deterministic tuple of matching experiments sorted by experiment_id.
        """
        results: list[ExperimentDesign] = []
        clean_hyp = hypothesis_id.strip() if hypothesis_id else None

        for exp in self._experiments.values():
            if status is not None and exp.status != status:
                continue
            if clean_hyp is not None and exp.hypothesis_id != clean_hyp:
                continue
            results.append(exp)

        results.sort(key=lambda e: e.experiment_id)
        return tuple(results)

    def _save_to_disk(self) -> None:
        """Write all experiments to persistent JSONL file."""
        if self._persistence_path is None:
            return
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        sorted_exps = sorted(self._experiments.values(), key=lambda e: e.experiment_id)
        with open(self._persistence_path, "w", encoding="utf-8") as f:
            for e in sorted_exps:
                f.write(e.model_dump_json() + "\n")

    def _load_from_disk(self) -> None:
        """Load experiments from persistent JSONL file."""
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        with open(self._persistence_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean:
                    continue
                try:
                    data = json.loads(clean)
                    exp = ExperimentDesign.model_validate(data)
                    self._experiments[exp.experiment_id] = exp
                except Exception as e:
                    raise JournalSerializationError(
                        f"Failed parsing experiment line {line_no}: {e}"
                    ) from e
