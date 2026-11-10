"""TOSO Guardian Domain Errors.

Defines the error hierarchy for portfolio-level risk controls,
intake gating, drawdown circuit breakers, and fail-closed UNKNOWN input handling.
"""


class GuardianRiskError(Exception):
    """Base exception for all Guardian risk-gating rejections."""


class PortfolioHeatExceededError(GuardianRiskError):
    """Raised when total open portfolio risk breaches max_portfolio_risk_pct."""


class SingleTradeRiskExceededError(GuardianRiskError):
    """Raised when a proposed single-trade risk breaches max_risk_per_trade_pct."""


class AggregatePositionRiskExceededError(GuardianRiskError):
    """Raised when aggregate risk on a symbol breaches max_aggregate_position_risk_pct."""


class PortfolioDrawdownHaltError(GuardianRiskError):
    """Raised when drawdown reaches or breaches emergency_drawdown_stop_pct."""


class GrossExposureLimitExceededError(GuardianRiskError):
    """Raised when total portfolio market exposure exceeds gross capital/leverage capacity."""


class UnknownPortfolioRiskInputError(GuardianRiskError):
    """Raised when input data is missing, non-positive, NaN, infinite, or ambiguous (INV-008)."""


class ConcentrationIntakeBlockedError(GuardianRiskError):
    """Raised when proposed intake breaches max_position_concentration_pct."""


class OrganicConcentrationAddProhibitedError(ConcentrationIntakeBlockedError):
    """Raised when attempting to add to a position that has organically breached concentration."""


class UnknownConcentrationInputError(GuardianRiskError):
    """Raised when concentration input data is missing, non-positive, NaN, or ambiguous."""


class CapitalSeparationError(GuardianRiskError):
    """Base exception for all capital separation and sleeve isolation violations."""


class CoreCapitalExhaustedError(CapitalSeparationError):
    """Raised when Core trading capacity is already exhausted or exceeded."""


class ReserveEncroachmentError(CapitalSeparationError):
    """Raised when proposed trade would encroach upon isolated Reserve capital (INV-012)."""


class BlackSwanEncroachmentError(CapitalSeparationError):
    """Raised when proposed trade would encroach into Black Swan capital (INV-013)."""


class UnknownCapitalInputError(GuardianRiskError):
    """Raised when capital separation input is non-positive, NaN, or infinite (INV-008)."""


class ExecutionGateError(GuardianRiskError):
    """Base exception for all Execution Gate rejections and blocks."""


class SystemFeedCheckFailedError(ExecutionGateError):
    """Raised when system operational, broker connectivity, or feed freshness checks fail."""


class IntakeRiskCheckFailedError(ExecutionGateError):
    """Raised when portfolio risk, concentration, capital, or winner checks fail."""


class ExecutionSafetyCheckFailedError(ExecutionGateError):
    """Raised when order identity, stop protection, or order-in-flight conflicts fail."""


class ExecutionBypassAttemptError(ExecutionGateError):
    """Raised when an order submission attempts to bypass Gate evaluation (INV-007)."""


class UnknownGateInputError(ExecutionGateError):
    """Raised when gate input data is missing, non-positive, NaN, or ambiguous (INV-008)."""


class ProtectionContractError(GuardianRiskError):
    """Base exception for all protection contract and stop verification failures."""


class UnverifiedProtectionError(ProtectionContractError):
    """Raised when attempting additional exposure on a position without verified stop (AC-001)."""


class ProtectionRecoveryFailedError(ProtectionContractError):
    """Raised when deterministic stop recovery fails and triggers safety liquidation (AC-003)."""


class InvalidProtectionStateError(ProtectionContractError):
    """Raised when protection state input is non-positive, NaN, or ambiguous (INV-008)."""


class EmergencyStopError(GuardianRiskError):
    """Base exception for all emergency stop and safety liquidation failures (FR-015)."""


class EmergencyStopActiveError(EmergencyStopError):
    """Raised when an operation is rejected because emergency stop is active."""


class EmergencyLiquidationFailedError(EmergencyStopError):
    """Raised when deterministic emergency liquidation encounters an unrecoverable failure."""


class UnknownEmergencyInputError(EmergencyStopError):
    """Raised when emergency input data is missing, non-positive, NaN, or ambiguous (INV-008)."""


class InvalidEmergencyModeTransitionError(EmergencyStopError):
    """Raised when an invalid emergency mode transition is attempted."""
