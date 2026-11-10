"""TOSO Winner Continuation & Anti-Take-Profit Rule Errors.

Defines domain-specific exceptions for enforcing Philosophy §2.2 ("Let Winners Run"),
Philosophy §2.6 ("Concentration Controls New Exposure"), FR-007, and FR-008:
- Prohibits fixed profit targets or target prices.
- Prohibits premature trimming / scaling out to "lock in profits".
- Prohibits forced liquidation or rebalancing on organic concentration growth.
- Prohibits discretionary or agent overrides of valid winners.
"""


class ContinuationError(Exception):
    """Base exception for all winner continuation and anti-take-profit rule violations."""


class TakeProfitProhibitedError(ContinuationError):
    """Raised when an attempt is made to set or execute a profit target on a Core position.

    Enforces Philosophy §2.2 and FR-007: Core positions exit solely on strategy invalidation;
    fixed profit targets or target-price exits are strictly prohibited.
    """


class PrematureTrimmingProhibitedError(ContinuationError):
    """Raised when an attempt is made to trim, scale out, or partially exit a winning position.

    Enforces Philosophy §2.2 and FR-007: Profitable positions must not be trimmed solely
    because they have appreciated substantially or to 'lock in gains'.
    """


class ForcedConcentrationExitProhibitedError(ContinuationError):
    """Raised when an attempt is made to force liquidation or trimming on organic concentration.

    Enforces Philosophy §2.6 and FR-008: Concentration is an intake constraint, not a
    forced-exit mechanism. Organic price appreciation exceeding concentration caps must not
    trigger forced sales.
    """


class DiscretionaryExitProhibitedError(ContinuationError):
    """Raised when an agent or caller attempts to exit an uninvalidated winning position.

    Enforces Philosophy §2.2, §2.8, and FR-007: Valid winners run until strategy invalidation.
    Discretionary or agent profit judgments cannot trigger an exit.
    """


class InvalidContinuationStateError(ContinuationError):
    """Raised when continuation evaluation is invoked on an invalid position lifecycle state."""
