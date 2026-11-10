"""TOSO Black Swan Defense Subsystem Errors.

Defines the error taxonomy for defensive capital separation, sizing limits,
and Core/Black Swan boundary enforcement (FR-010, FR-011, INV-013).
"""


class BlackSwanDefenseError(Exception):
    """Base exception for all Black Swan Defense subsystem errors."""


class BlackSwanCapitalExhaustedError(BlackSwanDefenseError):
    """Raised when proposed defensive allocation exceeds available Black Swan capital budget."""


class BlackSwanSleeveBreachError(BlackSwanDefenseError):
    """Raised when an action breaches sleeve boundaries between Core, Reserve, and Black Swan."""


class UnknownDefenseInputError(BlackSwanDefenseError):
    """Raised when defensive input data is missing, non-positive, NaN, or corrupted (INV-008)."""


class DisallowedDefensiveInstrumentError(BlackSwanDefenseError):
    """Raised when attempting to deploy an unapproved defensive instrument (INV-008)."""


class DefensiveUniverseLiquidityError(BlackSwanDefenseError):
    """Raised when a defensive instrument fails volume or price liquidity constraints (INV-008)."""


class CoreDefensiveUniverseCollisionError(BlackSwanDefenseError):
    """Raised when a Core equity is submitted to Black Swan or vice-versa (FR-011, INV-013)."""


class DefensiveTriggerError(BlackSwanDefenseError):
    """Base exception for defensive trigger condition evaluation errors."""


class DefensiveTriggerConditionNotMetError(DefensiveTriggerError):
    """Raised when defensive trade is requested without an active valid trigger condition."""


class DefensiveSizingExhaustedError(BlackSwanDefenseError):
    """Raised when defensive sizing exceeds remaining defensive sleeve budget."""


class DefensiveSubAllocationCapExceededError(BlackSwanDefenseError):
    """Raised when defensive sizing exceeds the instrument sub-allocation limit."""


class DefensiveExitError(BlackSwanDefenseError):
    """Base exception for defensive exit and de-escalation errors."""


class DefensivePositionNotFoundError(DefensiveExitError):
    """Raised when attempting to exit a non-existent or untracked defensive position."""


class InvalidDefensiveExitActionError(DefensiveExitError):
    """Raised when an invalid or unsupported exit action or ratio is provided."""
