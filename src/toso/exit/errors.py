"""TOSO Exit & Invalidation Enforcement Errors.

Defines domain-specific exceptions for exit evaluation, invalidation breaches,
anti-averaging-down violations, and prohibited agent overrides.
"""


class ExitError(Exception):
    """Base exception for all exit-related failures."""


class InvalidationExitOverrideError(ExitError):
    """Raised when an entity attempts to cancel, delay, or override an invalidation exit."""


class AveragingDownProhibitedError(ExitError):
    """Raised when an attempt is made to add exposure to an invalidated or losing position."""


class InvalidPositionStateError(ExitError):
    """Raised when an invalid position lifecycle state transition is attempted."""
