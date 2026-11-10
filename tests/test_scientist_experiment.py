"""Tests for Story SCI-004: Experiment Design & Parameterization.

Verifies:
- FR-017: Reproducible research experiments linked to hypotheses.
- FR-018: Strict temporal data separation between in-sample and out-of-sample datasets.
- INV-014: Research experiments isolated from autonomous execution.
- INV-008: Fail-closed handling on corrupt, missing, or invalid inputs.
- INV-001, INV-006: Pure determinism and immutable audit receipts.
- Specification Section 23/24: Research quality, leakage prevention, and state transitions.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.scientist import (
    DataLeakageError,
    DatasetSplit,
    DuplicateExperimentError,
    ExperimentDesign,
    ExperimentNotFoundError,
    ExperimentReceipt,
    ExperimentRegistry,
    ExperimentStatus,
    InvalidExperimentDesignError,
    InvalidExperimentStateTransitionError,
    JournalSerializationError,
    ParameterVariation,
    UnknownScientistInputError,
    create_experiment_design,
    transition_experiment_status,
    validate_dataset_split,
)


@pytest.fixture
def test_timestamp() -> datetime:
    return datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def valid_dataset_split() -> DatasetSplit:
    return DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2025, 1, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2026, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD", "ETH/USD"),
    )


@pytest.fixture
def sample_variations() -> tuple[ParameterVariation, ...]:
    return (
        ParameterVariation(
            parameter_name="breakout_lookback_bars",
            baseline_value=20,
            candidate_value=25,
            rationale="Dampen noise in low-volatility chop regimes.",
        ),
        ParameterVariation(
            parameter_name="invalidation_atr_multiple",
            baseline_value=2.0,
            candidate_value=2.5,
            rationale="Give trend more breathing room before exit.",
        ),
    )


@pytest.fixture
def sample_experiment(
    test_timestamp: datetime,
    valid_dataset_split: DatasetSplit,
    sample_variations: tuple[ParameterVariation, ...],
) -> ExperimentDesign:
    return create_experiment_design(
        experiment_id="EXP-LOOKBACK-001",
        hypothesis_id="HYP-BREAKOUT-001",
        title="Breakout Lookback & ATR Sensitivity Study",
        description="Evaluate performance of 25-bar lookback vs 20-bar baseline across regimes.",
        baseline_config_version="1.0.0",
        parameter_variations=sample_variations,
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe_ratio", "max_drawdown", "profit_factor"),
        created_at=test_timestamp,
    )


def test_ac001_create_experiment_design(
    sample_experiment: ExperimentDesign,
    test_timestamp: datetime,
    valid_dataset_split: DatasetSplit,
) -> None:
    """AC-001: Formulates immutable ExperimentDesign with parameter variations and metrics."""
    assert sample_experiment.experiment_id == "EXP-LOOKBACK-001"
    assert sample_experiment.hypothesis_id == "HYP-BREAKOUT-001"
    assert sample_experiment.title == "Breakout Lookback & ATR Sensitivity Study"
    assert sample_experiment.baseline_config_version == "1.0.0"
    assert len(sample_experiment.parameter_variations) == 2
    assert sample_experiment.dataset_split == valid_dataset_split
    assert sample_experiment.target_metrics == ("sharpe_ratio", "max_drawdown", "profit_factor")
    assert sample_experiment.status == ExperimentStatus.DRAFT
    assert sample_experiment.created_at == test_timestamp
    assert sample_experiment.updated_at == test_timestamp


def test_ac002_data_leakage_detection_overlapping_dates() -> None:
    """AC-002: Rejects in-sample and out-of-sample date collisions (FR-018)."""
    # Case 1: in_sample_end_date == out_of_sample_end_date (zero validation window)
    split_zero_oos = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2025, 1, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2025, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )
    with pytest.raises(DataLeakageError, match="must strictly precede"):
        validate_dataset_split(split_zero_oos)

    # Case 2: in_sample_end_date > out_of_sample_end_date (inverted / overlapping)
    split_inverted = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2026, 1, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2025, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )
    with pytest.raises(DataLeakageError, match="must strictly precede"):
        validate_dataset_split(split_inverted)

    # Case 3: start_date >= in_sample_end_date (empty in-sample period)
    split_empty_is = DatasetSplit(
        start_date=datetime(2025, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2025, 1, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2026, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )
    with pytest.raises(DataLeakageError, match="start_date .* must precede"):
        validate_dataset_split(split_empty_is)


def test_ac003_legal_experiment_state_transitions(
    sample_experiment: ExperimentDesign, test_timestamp: datetime
) -> None:
    """AC-003: Successfully performs legal lifecycle state transitions."""
    # DRAFT -> CONFIGURED
    t1 = test_timestamp + timedelta(hours=1)
    configured = transition_experiment_status(
        sample_experiment, ExperimentStatus.CONFIGURED, t1, notes="Config finalized."
    )
    assert configured.status == ExperimentStatus.CONFIGURED
    assert configured.updated_at == t1
    assert "Config finalized." in configured.notes

    # CONFIGURED -> RUNNING
    t2 = t1 + timedelta(hours=1)
    running = transition_experiment_status(
        configured, ExperimentStatus.RUNNING, t2, notes="Backtest executing."
    )
    assert running.status == ExperimentStatus.RUNNING
    assert running.updated_at == t2

    # RUNNING -> COMPLETED
    t3 = t2 + timedelta(hours=2)
    completed = transition_experiment_status(
        running, ExperimentStatus.COMPLETED, t3, notes="All metrics calculated."
    )
    assert completed.status == ExperimentStatus.COMPLETED
    assert completed.updated_at == t3

    # RUNNING -> FAILED -> CONFIGURED (recovery loop)
    failed = transition_experiment_status(
        running, ExperimentStatus.FAILED, t3, notes="Simulation node timeout."
    )
    assert failed.status == ExperimentStatus.FAILED

    reconfigured = transition_experiment_status(
        failed, ExperimentStatus.CONFIGURED, t3 + timedelta(hours=1), notes="Retrying."
    )
    assert reconfigured.status == ExperimentStatus.CONFIGURED


def test_ac004_illegal_experiment_state_transitions(
    sample_experiment: ExperimentDesign, test_timestamp: datetime
) -> None:
    """AC-004: Stage-skipping and transitions from terminal states fail closed."""
    t1 = test_timestamp + timedelta(hours=1)

    # DRAFT -> RUNNING (cannot skip CONFIGURED)
    with pytest.raises(InvalidExperimentStateTransitionError, match="Illegal state transition"):
        transition_experiment_status(sample_experiment, ExperimentStatus.RUNNING, t1)

    # DRAFT -> COMPLETED (cannot skip straight to completed)
    with pytest.raises(InvalidExperimentStateTransitionError, match="Illegal state transition"):
        transition_experiment_status(sample_experiment, ExperimentStatus.COMPLETED, t1)

    # Terminal state transitions: COMPLETED -> RUNNING / CANCELLED
    configured = transition_experiment_status(sample_experiment, ExperimentStatus.CONFIGURED, t1)
    running = transition_experiment_status(
        configured, ExperimentStatus.RUNNING, t1 + timedelta(hours=1)
    )
    completed = transition_experiment_status(
        running, ExperimentStatus.COMPLETED, t1 + timedelta(hours=2)
    )

    with pytest.raises(InvalidExperimentStateTransitionError, match="Illegal state transition"):
        transition_experiment_status(completed, ExperimentStatus.RUNNING, t1 + timedelta(hours=3))

    with pytest.raises(InvalidExperimentStateTransitionError, match="Illegal state transition"):
        transition_experiment_status(completed, ExperimentStatus.CANCELLED, t1 + timedelta(hours=3))

    # Terminal CANCELLED -> DRAFT
    cancelled = transition_experiment_status(
        sample_experiment, ExperimentStatus.CANCELLED, t1 + timedelta(hours=1)
    )
    with pytest.raises(InvalidExperimentStateTransitionError, match="Illegal state transition"):
        transition_experiment_status(cancelled, ExperimentStatus.DRAFT, t1 + timedelta(hours=2))


def test_ac005_experiment_registry_lifecycle(
    sample_experiment: ExperimentDesign, test_timestamp: datetime
) -> None:
    """AC-005: Registers, updates, and tracks experiment lifecycle in ExperimentRegistry."""
    registry = ExperimentRegistry()
    assert registry.total_count == 0

    receipt = registry.register_experiment(sample_experiment, test_timestamp)
    assert isinstance(receipt, ExperimentReceipt)
    assert receipt.experiment_id == sample_experiment.experiment_id
    assert receipt.hypothesis_id == sample_experiment.hypothesis_id
    assert receipt.action == "REGISTERED"
    assert receipt.new_status == ExperimentStatus.DRAFT
    assert registry.total_count == 1

    fetched = registry.get_experiment("EXP-LOOKBACK-001")
    assert fetched == sample_experiment

    # Update experiment
    t1 = test_timestamp + timedelta(hours=2)
    configured = transition_experiment_status(sample_experiment, ExperimentStatus.CONFIGURED, t1)
    update_receipt = registry.update_experiment(configured, t1)
    assert update_receipt.action == "UPDATED"
    assert update_receipt.previous_status == ExperimentStatus.DRAFT
    assert update_receipt.new_status == ExperimentStatus.CONFIGURED
    assert registry.get_experiment("EXP-LOOKBACK-001").status == ExperimentStatus.CONFIGURED


def test_ac006_query_and_filter_experiments(
    valid_dataset_split: DatasetSplit,
    sample_variations: tuple[ParameterVariation, ...],
    test_timestamp: datetime,
) -> None:
    """AC-006: Deterministically queries and filters experiments by status and hypothesis ID."""
    registry = ExperimentRegistry()

    exp1 = create_experiment_design(
        experiment_id="EXP-001",
        hypothesis_id="HYP-A",
        title="Exp 1",
        description="First",
        baseline_config_version="1.0.0",
        parameter_variations=sample_variations,
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe_ratio",),
        created_at=test_timestamp,
        initial_status=ExperimentStatus.DRAFT,
    )
    exp2 = create_experiment_design(
        experiment_id="EXP-002",
        hypothesis_id="HYP-B",
        title="Exp 2",
        description="Second",
        baseline_config_version="1.0.0",
        parameter_variations=sample_variations,
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe_ratio",),
        created_at=test_timestamp,
        initial_status=ExperimentStatus.CONFIGURED,
    )
    exp3 = create_experiment_design(
        experiment_id="EXP-003",
        hypothesis_id="HYP-A",
        title="Exp 3",
        description="Third",
        baseline_config_version="1.0.0",
        parameter_variations=sample_variations,
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe_ratio",),
        created_at=test_timestamp,
        initial_status=ExperimentStatus.CONFIGURED,
    )

    registry.register_experiment(exp2, test_timestamp)
    registry.register_experiment(exp1, test_timestamp)
    registry.register_experiment(exp3, test_timestamp)

    # Filter by hypothesis
    hyp_a_exps = registry.list_experiments(hypothesis_id="HYP-A")
    assert len(hyp_a_exps) == 2
    assert [e.experiment_id for e in hyp_a_exps] == ["EXP-001", "EXP-003"]

    # Filter by status
    configured_exps = registry.list_experiments(status=ExperimentStatus.CONFIGURED)
    assert len(configured_exps) == 2
    assert [e.experiment_id for e in configured_exps] == ["EXP-002", "EXP-003"]

    # Combined filter
    combined = registry.list_experiments(status=ExperimentStatus.CONFIGURED, hypothesis_id="HYP-A")
    assert len(combined) == 1
    assert combined[0].experiment_id == "EXP-003"


def test_ac007_duplicate_and_missing_experiment_errors(
    sample_experiment: ExperimentDesign, test_timestamp: datetime
) -> None:
    """AC-007: Registering duplicate ID or querying missing ID fails closed."""
    registry = ExperimentRegistry()
    registry.register_experiment(sample_experiment, test_timestamp)

    with pytest.raises(DuplicateExperimentError, match="already registered"):
        registry.register_experiment(sample_experiment, test_timestamp)

    with pytest.raises(ExperimentNotFoundError, match="not found in registry"):
        registry.get_experiment("NON-EXISTENT-EXP")


def test_ac008_persistent_disk_registry(
    sample_experiment: ExperimentDesign,
    test_timestamp: datetime,
    tmp_path: Path,
) -> None:
    """AC-008: Supports optional JSONL disk persistence and deserialization."""
    persist_file = tmp_path / "experiments.jsonl"
    reg1 = ExperimentRegistry(persistence_path=persist_file)
    reg1.register_experiment(sample_experiment, test_timestamp)

    # Verify file was written
    assert persist_file.exists()

    # Load in new registry instance
    reg2 = ExperimentRegistry(persistence_path=persist_file)
    assert reg2.total_count == 1
    loaded = reg2.get_experiment("EXP-LOOKBACK-001")
    assert loaded == sample_experiment

    # Corrupt the file and verify fail-closed
    with open(persist_file, "a", encoding="utf-8") as f:
        f.write("{invalid-json-line\n")

    with pytest.raises(JournalSerializationError, match="Failed parsing experiment line"):
        ExperimentRegistry(persistence_path=persist_file)


def test_ac009_fail_closed_on_corrupt_unknown_inputs(
    valid_dataset_split: DatasetSplit,
    sample_variations: tuple[ParameterVariation, ...],
    test_timestamp: datetime,
) -> None:
    """AC-009: Invalid inputs, empty fields, or non-chronological transitions fail closed."""
    # Blank experiment ID
    with pytest.raises(UnknownScientistInputError, match="Field 'experiment_id'"):
        create_experiment_design(
            experiment_id="",
            hypothesis_id="HYP-001",
            title="Title",
            description="Desc",
            baseline_config_version="1.0.0",
            parameter_variations=sample_variations,
            dataset_split=valid_dataset_split,
            target_metrics=("sharpe",),
            created_at=test_timestamp,
        )

    # Empty variations
    with pytest.raises(InvalidExperimentDesignError, match="at least one parameter variation"):
        create_experiment_design(
            experiment_id="EXP-001",
            hypothesis_id="HYP-001",
            title="Title",
            description="Desc",
            baseline_config_version="1.0.0",
            parameter_variations=(),
            dataset_split=valid_dataset_split,
            target_metrics=("sharpe",),
            created_at=test_timestamp,
        )

    # Empty target metrics
    with pytest.raises(InvalidExperimentDesignError, match="at least one target metric"):
        create_experiment_design(
            experiment_id="EXP-001",
            hypothesis_id="HYP-001",
            title="Title",
            description="Desc",
            baseline_config_version="1.0.0",
            parameter_variations=sample_variations,
            dataset_split=valid_dataset_split,
            target_metrics=(),
            created_at=test_timestamp,
        )

    # Non-chronological transition timestamp
    valid_exp = create_experiment_design(
        experiment_id="EXP-001",
        hypothesis_id="HYP-001",
        title="Title",
        description="Desc",
        baseline_config_version="1.0.0",
        parameter_variations=sample_variations,
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe",),
        created_at=test_timestamp,
    )
    past_timestamp = test_timestamp - timedelta(seconds=1)
    with pytest.raises(UnknownScientistInputError, match="cannot precede last update"):
        transition_experiment_status(valid_exp, ExperimentStatus.CONFIGURED, past_timestamp)


def test_ac010_pure_determinism_and_immutability(
    sample_experiment: ExperimentDesign,
) -> None:
    """AC-010: Enforces frozen Pydantic models with extra='forbid'."""
    with pytest.raises(ValidationError):
        sample_experiment.title = "Mutated Title"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        ExperimentDesign.model_validate(
            {**sample_experiment.model_dump(), "rogue_execution_field": True}
        )
