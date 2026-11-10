"""TOSO Scientist Subsystem Errors.

Defines the error taxonomy for append-only journaling, observation records,
and research boundary enforcement (FR-016, FR-017, INV-014).
"""


class ScientistError(Exception):
    """Base exception for all Scientist subsystem errors."""


class JournalTamperError(ScientistError):
    """Raised when journal tampering, mutation, deletion, or broken chain is detected (FR-016)."""


class JournalSerializationError(ScientistError):
    """Raised when journal entry data cannot be deterministically serialized or deserialized."""


class UnknownScientistInputError(ScientistError):
    """Raised when scientist or journal inputs are missing, NaN, corrupt, or invalid (INV-008)."""


class ScientistExecutionBreachError(ScientistError):
    """Raised when Scientist code attempts to submit, cancel, or route orders (INV-014)."""


class UnauthorizedProductionChangeError(ScientistError):
    """Raised when an autonomous production modification is attempted without human approval."""


class InvalidApprovalTokenError(ScientistError):
    """Raised when a human approval token is expired, forged, or mismatched (INV-014)."""


class HypothesisError(ScientistError):
    """Base exception for research hypothesis errors."""


class HypothesisNotFoundError(HypothesisError):
    """Raised when looking up an unrecorded or missing hypothesis ID."""


class InvalidHypothesisStateTransitionError(HypothesisError):
    """Raised when an illegal lifecycle state transition is attempted on a hypothesis."""


class DuplicateHypothesisError(HypothesisError):
    """Raised when attempting to create a hypothesis with an ID that already exists."""


class ExperimentError(ScientistError):
    """Base exception for research experiment errors."""


class ExperimentNotFoundError(ExperimentError):
    """Raised when looking up an unrecorded or missing experiment ID."""


class InvalidExperimentStateTransitionError(ExperimentError):
    """Raised when an illegal lifecycle state transition is attempted on an experiment."""


class DuplicateExperimentError(ExperimentError):
    """Raised when attempting to register an experiment with an existing ID."""


class DataLeakageError(ExperimentError):
    """Raised when in-sample and out-of-sample periods overlap or violate split rules (FR-018)."""


class InvalidExperimentDesignError(ExperimentError):
    """Raised when experiment parameters, splits, or metrics are invalid or corrupt (INV-008)."""


class BacktestError(ScientistError):
    """Base exception for all backtesting engine errors."""


class InsufficientDataForBacktestError(BacktestError):
    """Raised when bar data is empty, insufficient for indicators, or out of range."""


class InvalidBacktestConfigurationError(BacktestError):
    """Raised when backtest parameters, capital, or overrides are invalid (INV-008)."""


class InSampleViolationError(BacktestError, DataLeakageError):
    """Raised when an in-sample backtest accesses or processes out-of-sample data (FR-018)."""


class OOSEvaluationError(ScientistError):
    """Base exception for all out-of-sample evaluation errors."""


class InsufficientOOSDataError(OOSEvaluationError):
    """Raised when out-of-sample bar data is missing or insufficient for evaluation."""


class OOSBoundaryViolationError(OOSEvaluationError, DataLeakageError):
    """Raised when data evaluated in OOS breaches [in_sample_end_date, out_of_sample_end_date)."""


class OverfittingDetectedError(OOSEvaluationError):
    """Raised when candidate strategy fails OOS validation due to severe degradation."""


class UnverifiedInSampleBacktestError(OOSEvaluationError):
    """Raised when attempting OOS evaluation on an experiment without verified in-sample results."""


class ProposalError(ScientistError):
    """Base exception for all research production proposal errors."""


class ProposalNotFoundError(ProposalError):
    """Raised when looking up an unrecorded or missing proposal ID."""


class DuplicateProposalError(ProposalError):
    """Raised when attempting to register a proposal with an existing ID."""


class InvalidProposalStateTransitionError(ProposalError):
    """Raised when an illegal lifecycle state transition is attempted on a proposal."""


class UnvalidatedProposalError(ProposalError):
    """Raised when attempting to formulate a proposal from an unvalidated OOS evaluation."""
