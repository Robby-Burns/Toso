"""TOSO Paper Validation Subsystem (Phase 6).

Deterministic simulation environment, paper broker engine, order execution,
and integration testing harnesses without live capital risk.
"""

from toso.paper.broker import PaperBroker, compute_paper_receipt_checksum
from toso.paper.errors import (
    DuplicatePaperOrderError,
    EmergencyKillSwitchActiveError,
    InsufficientPaperFundsError,
    InvalidPaperOrderError,
    InvalidPaperStateError,
    PaperOrderError,
    PaperOrderNotFoundError,
    PaperTradingError,
    UnknownPaperInputError,
)
from toso.paper.models import (
    PaperAccountState,
    PaperAction,
    PaperBrokerReceipt,
    PaperFill,
    PaperOrder,
    PaperPosition,
    PaperSimulationStepResult,
    PaperSimulationSummary,
    PaperTradeRecord,
)
from toso.paper.runner import PaperTradingRunner, compute_simulation_checksum

__all__ = [
    "DuplicatePaperOrderError",
    "EmergencyKillSwitchActiveError",
    "InsufficientPaperFundsError",
    "InvalidPaperOrderError",
    "InvalidPaperStateError",
    "PaperAccountState",
    "PaperAction",
    "PaperBroker",
    "PaperBrokerReceipt",
    "PaperFill",
    "PaperOrder",
    "PaperOrderError",
    "PaperOrderNotFoundError",
    "PaperPosition",
    "PaperSimulationStepResult",
    "PaperSimulationSummary",
    "PaperTradeRecord",
    "PaperTradingRunner",
    "PaperTradingError",
    "UnknownPaperInputError",
    "compute_paper_receipt_checksum",
    "compute_simulation_checksum",
]
