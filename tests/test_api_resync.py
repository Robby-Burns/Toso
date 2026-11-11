"""Tests for WebSocket Sequence Tracking and Resynchronization (UI-002).

Verifies monotonic sequence counter incrementation, event replay buffer retrieval,
and state snapshot catch-up under network reconnection scenarios.
"""

import pytest
from fastapi.testclient import TestClient

from toso.api.app import create_app
from toso.api.models import EnvironmentMode
from toso.api.state import APIServerState
from toso.config.loader import load_config_from_file
from toso.live.arming import LiveArmingGate


@pytest.fixture
def api_state() -> APIServerState:
    config = load_config_from_file("config/core_config_v1.yaml")
    gate = LiveArmingGate()
    return APIServerState(config=config, arming_gate=gate, environment=EnvironmentMode.PAPER)


@pytest.fixture
def client(api_state: APIServerState) -> TestClient:
    app = create_app(state=api_state)
    return TestClient(app)


@pytest.mark.anyio
async def test_websocket_broadcast_increments_monotonic_sequences(
    api_state: APIServerState,
) -> None:
    """AC-002: Emitted events increment sequence IDs monotonically."""
    initial_seq = api_state.ws_hub.current_sequence_id

    # Emit three distinct broadcast events
    msg1 = await api_state.ws_hub.broadcast("TICK", {"symbol": "AAPL", "price": 226.50})
    msg2 = await api_state.ws_hub.broadcast("GATE_UPDATE", {"status": "PASS"})
    msg3 = await api_state.ws_hub.broadcast("HEARTBEAT", {"latency": 11.2})

    assert msg1.sequence_id == initial_seq + 1
    assert msg2.sequence_id == initial_seq + 2
    assert msg3.sequence_id == initial_seq + 3
    assert api_state.ws_hub.current_sequence_id == initial_seq + 3


@pytest.mark.anyio
async def test_reconnect_sync_endpoint_replays_missed_events(
    client: TestClient, api_state: APIServerState
) -> None:
    """AC-002: Client reconnecting with older sequence ID receives missed events."""
    # Emit events
    await api_state.ws_hub.broadcast("EVENT_A", {"index": 1})
    await api_state.ws_hub.broadcast("EVENT_B", {"index": 2})
    await api_state.ws_hub.broadcast("EVENT_C", {"index": 3})

    current = api_state.ws_hub.current_sequence_id

    # Request sync since (current - 2): should receive exactly the last 2 events
    since_seq = current - 2
    res = client.get(f"/api/state/sync?since_seq={since_seq}")
    assert res.status_code == 200
    data = res.json()

    assert data["current_sequence_id"] == current
    missed = data["missed_events"]
    assert len(missed) == 2
    assert missed[0]["event_type"] == "EVENT_B"
    assert missed[1]["event_type"] == "EVENT_C"
    assert data["state"]["top_bar"]["environment"] == "PAPER"


def test_ui_shell_serves_updated_elements(client: TestClient) -> None:
    """AC-001/003/004: Root GET / serves the enhanced UI shell with drawer and banners."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="connection-alert-banner"' in html
    assert 'id="live-warning-banner"' in html
    assert 'id="position-drawer"' in html
    assert "toggleEnvironment" in html
    assert "playDisarmAudioAlert" in html
