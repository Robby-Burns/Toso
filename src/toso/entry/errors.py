"""TOSO Entry and Invalidation Error Hierarchy.

Defines domain-specific exceptions for Core entry orders, invalidation definitions,
anti-loosening violations, and unauthorized agent overrides.
"""


class EntryError(Exception):
    """Base exception for all entry and invalidation errors."""


class MissingInvalidationError(EntryError):
    """Raised when an entry order lacks a required deterministic invalidation."""


class InvalidInvalidationError(EntryError):
    """Raised when an invalidation price violates mathematical or boundary invariants."""


class InvalidationLooseningError(EntryError):
    """Raised when an attempt is made to loosen or widen an established invalidation stop."""


class AgentOverrideRejectedError(EntryError):
    """Raised when an agent or external caller attempts to override an established invalidation."""


class InvalidEntryOrderError(EntryError):
    """Raised when entry order parameters are malformed, non-positive, or inconsistent."""
