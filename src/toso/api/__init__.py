"""TOSO API Package.

Provides the FastAPI application, state management, and real-time WebSocket hub.
"""

from toso.api.app import create_app
from toso.api.models import (
    BrokerLinkState,
    CapitalDistribution,
    EmergencyDisarmRequest,
    EmergencyDisarmResponse,
    EnvironmentMode,
    ExecutionAuthorization,
    GuardianCheckItem,
    GuardianCheckStatus,
    GuardianSummaryResponse,
    OpenPositionItem,
    SystemStateResponse,
    TopBarTelemetry,
    WebSocketTelemetryMessage,
)
from toso.api.state import APIServerState
from toso.api.ws import WebSocketHub

__all__ = [
    "APIServerState",
    "BrokerLinkState",
    "CapitalDistribution",
    "EmergencyDisarmRequest",
    "EmergencyDisarmResponse",
    "EnvironmentMode",
    "ExecutionAuthorization",
    "GuardianCheckItem",
    "GuardianCheckStatus",
    "GuardianSummaryResponse",
    "OpenPositionItem",
    "SystemStateResponse",
    "TopBarTelemetry",
    "WebSocketHub",
    "WebSocketTelemetryMessage",
    "create_app",
]
