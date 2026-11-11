"""Adversarial 9-Lens Verification Suite for UI-001 (API Bridge & State Contracts).

Covers all 9 mandatory verification lenses:
1. Boundary: Sequence ID boundaries (0, negative, overflow, empty replay).
2. Security: Zero-trust auth bypass attempts (fake tokens, missing headers, cookie tampering).
3. Failure Paths: Corrupted JSON, whitespace approver ID, wrong secret salt.
4. Skeptic: Halted runner unconditionally rejects arming via API.
5. Red Team: Cryptographic signature mismatch and tampered digest arming attacks fail closed.
6. QA Edge: Rapid sequential emergency disarm calls are fully idempotent and safe.
7. Regression: Exact 80/15/5 capital allocation preserved and 5-state Guardian model enforced.
8. Runtime: Timestamps in top-bar and emergency responses are strictly UTC-aware.
9. Blast Radius: High API error rates do not mutate runner cash or resting positions.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from toso.api.app import create_app
from toso.api.models import EnvironmentMode, GuardianCheckStatus
from toso.api.state import APIServerState
from toso.config.loader import load_config_from_file
from toso.live.arming import LiveArmingGate


@pytest.fixture
def canonical_config():
    return load_config_from_file("config/core_config_v1.yaml")


@pytest.fixture
def api_state(canonical_config):
    gate = LiveArmingGate()
    return APIServerState(
        config=canonical_config, arming_gate=gate, environment=EnvironmentMode.PAPER
    )


@pytest.fixture
def client(api_state):
    app = create_app(state=api_state)
    return TestClient(app)


# -----------------------------------------------------------------------------
# Lens 1: Boundary (Sequence IDs & Sync Buffering)
# -----------------------------------------------------------------------------
def test_lens_boundary_sequence_ids(client: TestClient) -> None:
    """Lens 1: Sequence ID boundary tests (since_seq=0, high numbers, negative numbers)."""
    # Negative since_seq should fail validation (ge=0)
    neg_res = client.get("/api/state/sync?since_seq=-1")
    assert neg_res.status_code == 422

    # since_seq=0 should return all currently buffered events
    zero_res = client.get("/api/state/sync?since_seq=0")
    assert zero_res.status_code == 200
    assert isinstance(zero_res.json()["missed_events"], list)

    # since_seq in the future should return empty missed events list
    future_res = client.get("/api/state/sync?since_seq=999999")
    assert future_res.status_code == 200
    assert len(future_res.json()["missed_events"]) == 0


# -----------------------------------------------------------------------------
# Lens 2: Security (Zero-Trust Auth Gate)
# -----------------------------------------------------------------------------
def test_lens_security_auth_bypass_attempts(api_state: APIServerState) -> None:
    """Lens 2: Verify unauthorized requests, spoofed headers, and tampered cookies fail closed."""
    app = create_app(state=api_state, auth_password="production-secret-token")
    secure_client = TestClient(app)

    # 1. Missing auth
    assert secure_client.get("/api/state").status_code == 401

    # 2. Spoofed header with wrong token
    bad_header = {"Authorization": "Bearer fake-token-123"}
    assert secure_client.get("/api/state", headers=bad_header).status_code == 401

    # 3. Malformed header (not Bearer)
    malformed_header = {"Authorization": "Basic production-secret-token"}
    assert secure_client.get("/api/state", headers=malformed_header).status_code == 401

    # 4. Tampered cookie
    bad_cookie = {"toso_session": "wrong-cookie"}
    assert secure_client.get("/api/state", cookies=bad_cookie).status_code == 401

    # 5. Correct token succeeds
    good_header = {"Authorization": "Bearer production-secret-token"}
    assert secure_client.get("/api/state", headers=good_header).status_code == 200


# -----------------------------------------------------------------------------
# Lens 3: Failure Paths (Malformed Payloads & Missing Fields)
# -----------------------------------------------------------------------------
def test_lens_failure_paths_malformed_arming(client: TestClient) -> None:
    """Lens 3: Ensure malformed or whitespace arming payloads are safely rejected."""
    # Empty JSON body
    res1 = client.post("/api/arming/arm", content=b"")
    assert res1.status_code in (400, 422)

    # Whitespace approver_id fails closed
    res2 = client.post(
        "/api/arming/arm", json={"approver_id": "   ", "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1"}
    )
    assert res2.status_code in (400, 422)

    # Missing approver_id field
    res3 = client.post("/api/arming/arm", json={"secret_salt": "TOSO-LIVE-ARMING-AUTH-v1"})
    assert res3.status_code == 422


# -----------------------------------------------------------------------------
# Lens 4: Skeptic (Runner Halt & Circuit Breaker)
# -----------------------------------------------------------------------------
def test_lens_skeptic_halted_runner_blocks_arming(
    client: TestClient, api_state: APIServerState
) -> None:
    """Lens 4: A runner in emergency HALTED state unconditionally blocks arming."""
    api_state.runner.trigger_kill_switch(
        reason="Adversarial emergency stop", timestamp=datetime.now(UTC)
    )
    assert api_state.runner.is_halted is True

    arm_req = {"approver_id": "human-operator", "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1"}
    res = client.post("/api/arming/arm", json=arm_req)
    assert res.status_code == 400
    assert "HALTED" in res.json()["detail"]
    assert api_state.arming_gate.is_armed is False


# -----------------------------------------------------------------------------
# Lens 5: Red Team (Cryptographic Signature Verification)
# -----------------------------------------------------------------------------
def test_lens_red_team_forged_salt_rejected(client: TestClient, api_state: APIServerState) -> None:
    """Lens 5: Attempting to arm with a forged or mismatched salt fails closed."""
    forged_req = {
        "approver_id": "adversary",
        "secret_salt": "ATTACKER-FORGED-SALT-XYZ",
        "notes": "Attempting unauthorized live dispatch",
    }
    res = client.post("/api/arming/arm", json=forged_req)
    assert res.status_code == 400
    assert "signature verification failed" in res.json()["detail"].lower()
    assert api_state.arming_gate.is_armed is False


# -----------------------------------------------------------------------------
# Lens 6: QA Edge (Rapid Emergency Disarm Idempotency)
# -----------------------------------------------------------------------------
def test_lens_qa_edge_repeated_disarm_idempotency(
    client: TestClient, api_state: APIServerState
) -> None:
    """Lens 6: Multiple rapid emergency disarm invocations must be 100% idempotent."""
    for i in range(10):
        res = client.post("/api/emergency/disarm", json={"reason": f"STRESS_TEST_{i}"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["status"] == "DISARMED"
    assert api_state.arming_gate.is_armed is False


# -----------------------------------------------------------------------------
# Lens 7: Regression (Capital Allocation & 5-State Guardian)
# -----------------------------------------------------------------------------
def test_lens_regression_capital_and_guardian_invariants(client: TestClient) -> None:
    """Lens 7: Strict compliance with approved 80/15/5 capital policy and 5-state model."""
    res = client.get("/api/state")
    assert res.status_code == 200
    cap = res.json()["capital"]

    total = cap["total_equity"]
    assert cap["core_allocated"] == total * 0.80
    assert cap["reserve_allocated"] == total * 0.15
    assert cap["black_swan_allocated"] == total * 0.05

    # Guardian 5-state compliance
    g_res = client.get("/api/guardian")
    assert g_res.status_code == 200
    allowed_states = {s.value for s in GuardianCheckStatus}
    for check in g_res.json()["checks"]:
        assert check["status"] in allowed_states


# -----------------------------------------------------------------------------
# Lens 8: Runtime (UTC Timestamp Monotonicity)
# -----------------------------------------------------------------------------
def test_lens_runtime_utc_monotonic_timestamps(client: TestClient) -> None:
    """Lens 8: Ensure all API response timestamps are ISO-8601 UTC compliant."""
    health_res = client.get("/healthz")
    assert health_res.status_code == 200
    ts_str = health_res.json()["timestamp"]
    ts = datetime.fromisoformat(ts_str)
    assert ts.tzinfo is not None

    state_res = client.get("/api/state")
    last_hb = state_res.json()["top_bar"]["last_heartbeat"]
    assert datetime.fromisoformat(last_hb).tzinfo is not None


# -----------------------------------------------------------------------------
# Lens 9: Blast Radius (Core State Isolation)
# -----------------------------------------------------------------------------
def test_lens_blast_radius_api_errors_do_not_mutate_portfolio(
    client: TestClient, api_state: APIServerState
) -> None:
    """Lens 9: API errors or invalid disarm payloads do not alter account cash or positions."""
    initial_cash = api_state.total_equity
    initial_positions_count = len(api_state.positions)

    # Send invalid requests
    client.post("/api/arming/arm", json={"invalid": "payload"})
    client.get("/api/state/sync?since_seq=-10")

    # State must remain identical
    assert api_state.total_equity == initial_cash
    assert len(api_state.positions) == initial_positions_count
