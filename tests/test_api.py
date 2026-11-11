"""Tests for TOSO API & WebSocket Bridge.

Verifies decoupled top-bar telemetry, 80/15/5 capital allocation, 5-state Guardian checks,
instantaneous emergency disarm, cryptographic arming, authentication, and WebSocket sequencing.
"""

import pytest
from fastapi.testclient import TestClient

from toso.api.app import create_app
from toso.api.models import EnvironmentMode, GuardianCheckStatus
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


def test_health_check(client: TestClient) -> None:
    """Verify /healthz returns 200 OK."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "toso-api"


def test_ui_static_index_serving(client: TestClient) -> None:
    """Verify web/out/index.html is served at root GET / with decoupled UI elements."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    html = response.text
    assert "TOSO Trading Console" in html
    assert 'id="pill-env"' in html
    assert 'id="btn-emergency-disarm"' in html
    assert 'id="slider-track"' in html


def test_system_state_endpoint(client: TestClient) -> None:
    """Verify /api/state returns decoupled top-bar and 80/15/5 capital allocation."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()

    # Top-bar decoupled checks
    top_bar = data["top_bar"]
    assert top_bar["environment"] == "PAPER"
    assert top_bar["authorization"] == "DISARMED"
    assert top_bar["broker_link"] == "CONNECTED"
    assert top_bar["reconciliation"] == "RECONCILED"

    # Capital allocation check: 80% Core / 15% Reserve / 5% Black Swan
    cap = data["capital"]
    assert cap["total_equity"] == 100_000.00
    assert cap["core_allocated"] == 80_000.00
    assert cap["reserve_allocated"] == 15_000.00
    assert cap["black_swan_allocated"] == 5_000.00
    assert cap["deployed_capital"] > 0
    assert cap["available_capital"] == cap["core_allocated"] - cap["deployed_capital"]


def test_guardian_5_state_summary(client: TestClient) -> None:
    """Verify /api/guardian returns checks across all 3 domains with 5-state support."""
    response = client.get("/api/guardian")
    assert response.status_code == 200
    data = response.json()

    domains = data["domain_statuses"]
    assert "SYSTEM_AND_FEED" in domains
    assert "INTAKE_AND_RISK" in domains
    assert "EXECUTION_SAFETY" in domains

    checks = data["checks"]
    assert len(checks) >= 6
    for c in checks:
        assert c["status"] in [
            GuardianCheckStatus.PASS,
            GuardianCheckStatus.FAIL,
            GuardianCheckStatus.UNKNOWN,
            GuardianCheckStatus.STALE,
            GuardianCheckStatus.BLOCKED,
        ]


def test_instantaneous_emergency_disarm(client: TestClient, api_state: APIServerState) -> None:
    """Verify emergency disarm takes effect immediately without friction."""
    # Pre-arm the gate
    preflight = api_state.run_preflight_checks()
    from toso.live.arming import create_human_approval_token
    from toso.live.matrix import AcceptanceMatrixAuditor
    from toso.live.resolution import LiveReadinessResolver

    matrix = AcceptanceMatrixAuditor.audit_matrix()
    readiness = LiveReadinessResolver.resolve_all_questions(core_config=api_state.config)
    token = create_human_approval_token(
        approver_id="test-operator",
        config_version=api_state.config.metadata.version,
        acceptance_matrix_digest=matrix.matrix_digest,
        preflight_receipt_hash=preflight.receipt_hash,
    )
    api_state.arming_gate.arm_system(
        human_token=token,
        preflight_receipt=preflight,
        matrix_receipt=matrix,
        readiness_receipt=readiness,
    )
    assert api_state.arming_gate.is_armed is True

    # Hit emergency disarm
    response = client.post("/api/emergency/disarm", json={"reason": "CRITICAL_DRIFT"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "DISARMED"
    assert data["reason"] == "CRITICAL_DRIFT"

    # Verify gate is immediately disarmed
    assert api_state.arming_gate.is_armed is False


def test_preflight_endpoint(client: TestClient) -> None:
    """Verify /api/arming/preflight runs the 7 mandatory pre-flight checks."""
    response = client.post("/api/arming/preflight")
    assert response.status_code == 200
    data = response.json()
    assert data["all_passed"] is True
    assert len(data["checks"]) == 7
    assert data["receipt_hash"] != ""


def test_live_arming_success_and_rejection(client: TestClient) -> None:
    """Verify arming with valid salt succeeds and invalid salt/approver fails closed."""
    # Invalid approver_id (empty)
    bad_req = {"approver_id": "   ", "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1"}
    bad_res = client.post("/api/arming/arm", json=bad_req)
    assert bad_res.status_code == 422 or bad_res.status_code == 400

    # Invalid secret salt
    wrong_salt_req = {"approver_id": "operator-1", "secret_salt": "WRONG_SALT"}
    wrong_res = client.post("/api/arming/arm", json=wrong_salt_req)
    assert wrong_res.status_code == 400

    # Valid arming request
    valid_req = {"approver_id": "operator-1", "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1"}
    good_res = client.post("/api/arming/arm", json=valid_req)
    assert good_res.status_code == 200
    data = good_res.json()
    assert data["success"] is True
    assert data["mode"] == "LIVE_ACTIVE"
    assert data["arming_id"].startswith("ARM-")


def test_auth_middleware() -> None:
    """Verify zero-trust authentication middleware protects endpoints when configured."""
    app = create_app(auth_password="secret-test-password")
    authed_client = TestClient(app)

    # Health check is exempt
    assert authed_client.get("/healthz").status_code == 200

    # Protected endpoint without auth fails
    unauth_res = authed_client.get("/api/state")
    assert unauth_res.status_code == 401

    # Protected endpoint with valid Bearer header succeeds
    bearer_res = authed_client.get(
        "/api/state", headers={"Authorization": "Bearer secret-test-password"}
    )
    assert bearer_res.status_code == 200

    # Protected endpoint with valid cookie succeeds
    cookie_res = authed_client.get("/api/state", cookies={"toso_session": "secret-test-password"})
    assert cookie_res.status_code == 200


def test_websocket_and_resync(client: TestClient, api_state: APIServerState) -> None:
    """Verify WebSocket sends initial snapshot and /api/state/sync returns missed events."""
    with client.websocket_connect("/ws") as ws:
        init_msg = ws.receive_json()
        assert init_msg["event_type"] == "INITIAL_STATE"
        assert init_msg["payload"]["top_bar"]["environment"] == "PAPER"

        # Ping-pong check
        ws.send_text("ping")
        assert ws.receive_text() == "pong"

    # Now verify event syncing
    sync_res = client.get("/api/state/sync?since_seq=0")
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert "current_sequence_id" in sync_data
    assert "state" in sync_data
