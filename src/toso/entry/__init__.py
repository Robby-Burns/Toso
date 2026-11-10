"""TOSO Entry & Invalidation Module.

Exports domain models, deterministic evaluation services, and error classes
for Core trade entry and invalidation enforcement.
"""

from toso.entry.errors import (
    AgentOverrideRejectedError,
    EntryError,
    InvalidationLooseningError,
    InvalidEntryOrderError,
    InvalidInvalidationError,
    MissingInvalidationError,
)
from toso.entry.evaluator import (
    calculate_deterministic_invalidation,
    create_entry_from_signal,
    ratchet_invalidation,
    reject_agent_invalidation_override,
)
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)

__all__ = [
    "AgentOverrideRejectedError",
    "CoreEntryOrder",
    "EntryError",
    "GapSlippageRiskModel",
    "InvalidEntryOrderError",
    "InvalidInvalidationError",
    "InvalidationDefinition",
    "InvalidationLooseningError",
    "MissingInvalidationError",
    "calculate_deterministic_invalidation",
    "create_entry_from_signal",
    "ratchet_invalidation",
    "reject_agent_invalidation_override",
]
