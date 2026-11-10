"""TOSO Black Swan Defense Subsystem.

Independent defensive and convex portfolio tail-risk protection (Phase 4).
Enforces capital separation, defensive sizing, instrument universe control,
and de-escalation without encroaching on Core trend-following (FR-010, FR-011).
"""

from toso.defense.capital import (
    calculate_black_swan_capital,
    enforce_black_swan_intake_safety,
    evaluate_black_swan_capital_intake,
)
from toso.defense.errors import (
    BlackSwanCapitalExhaustedError,
    BlackSwanDefenseError,
    BlackSwanSleeveBreachError,
    CoreDefensiveUniverseCollisionError,
    DefensiveExitError,
    DefensivePositionNotFoundError,
    DefensiveSizingExhaustedError,
    DefensiveSubAllocationCapExceededError,
    DefensiveTriggerConditionNotMetError,
    DefensiveTriggerError,
    DefensiveUniverseLiquidityError,
    DisallowedDefensiveInstrumentError,
    InvalidDefensiveExitActionError,
    UnknownDefenseInputError,
)
from toso.defense.exit import (
    calculate_deescalation_plan,
    enforce_defensive_exit,
    evaluate_defensive_position_exit,
)
from toso.defense.models import (
    BlackSwanCapitalAssessment,
    BlackSwanCapitalSnapshot,
    BlackSwanIntakeStatus,
    DefensiveDeescalationPlan,
    DefensiveEligibilityReceipt,
    DefensiveEligibilityStatus,
    DefensiveExitAction,
    DefensiveExitReason,
    DefensiveExitReceipt,
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensivePosition,
    DefensiveSizingReceipt,
    DefensiveTriggerCondition,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    DefensiveUniverseConfig,
)
from toso.defense.sizing import (
    calculate_defensive_position_size,
    enforce_defensive_position_sizing,
    evaluate_defensive_trigger,
)
from toso.defense.universe import (
    enforce_defensive_instrument_eligibility,
    evaluate_defensive_instrument_eligibility,
    load_canonical_defensive_universe,
)

__all__ = [
    "BlackSwanCapitalAssessment",
    "BlackSwanCapitalExhaustedError",
    "BlackSwanCapitalSnapshot",
    "BlackSwanDefenseError",
    "BlackSwanIntakeStatus",
    "BlackSwanSleeveBreachError",
    "CoreDefensiveUniverseCollisionError",
    "DefensiveDeescalationPlan",
    "DefensiveEligibilityReceipt",
    "DefensiveEligibilityStatus",
    "DefensiveExitAction",
    "DefensiveExitError",
    "DefensiveExitReceipt",
    "DefensiveExitReason",
    "DefensiveInstrumentSpec",
    "DefensiveInstrumentType",
    "DefensivePosition",
    "DefensivePositionNotFoundError",
    "DefensiveSizingExhaustedError",
    "DefensiveSizingReceipt",
    "DefensiveSubAllocationCapExceededError",
    "DefensiveTriggerCondition",
    "DefensiveTriggerConditionNotMetError",
    "DefensiveTriggerError",
    "DefensiveTriggerSignal",
    "DefensiveTriggerType",
    "DefensiveUniverseConfig",
    "DefensiveUniverseLiquidityError",
    "DisallowedDefensiveInstrumentError",
    "InvalidDefensiveExitActionError",
    "UnknownDefenseInputError",
    "calculate_black_swan_capital",
    "calculate_deescalation_plan",
    "calculate_defensive_position_size",
    "enforce_black_swan_intake_safety",
    "enforce_defensive_exit",
    "enforce_defensive_instrument_eligibility",
    "enforce_defensive_position_sizing",
    "evaluate_black_swan_capital_intake",
    "evaluate_defensive_instrument_eligibility",
    "evaluate_defensive_position_exit",
    "evaluate_defensive_trigger",
    "load_canonical_defensive_universe",
]
