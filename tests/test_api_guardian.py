"""Tests for Guardian 5-State Risk Inspector and Black Swan Defense (UI-003).

Verifies Black Swan capital ring-fencing (5%), defensive universe mutual exclusivity,
fail-closed simulation scenarios, and valid winner concentration protection.
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


def test_black_swan_defense_endpoint(client: TestClient) -> None:
    """AC-001/002: Verify Black Swan capital ring-fencing and universe mutual exclusivity."""
    res = client.get("/api/guardian/defense")
    assert res.status_code == 200
    data = res.json()

    # 5% capital ring-fencing
    assert data["allocated_pct"] == 0.05
    assert data["allocated_capital"] == 5_000.00
    assert data["is_ring_fenced"] is True
    assert data["status"] == "STANDBY"

    # Mutual exclusivity with core equities
    assert data["mutual_exclusivity_verified"] is True
    assert data["collision_count"] == 0
    core_syms = set(data["core_symbols"])
    assert "AAPL" in core_syms
    assert "NVDA" in core_syms

    # Canonical defensive instruments
    def_symbols = {d["symbol"] for d in data["defensive_symbols"]}
    assert "SH" in def_symbols
    assert "PSQ" in def_symbols
    assert "SPXU" in def_symbols
    assert "VIXY" in def_symbols
    assert "BIL" in def_symbols

    # Zero overlap invariant (INV-013)
    assert len(core_syms.intersection(def_symbols)) == 0


def test_guardian_simulation_stale_feed(client: TestClient, api_state: APIServerState) -> None:
    """AC-005: Simulating a stale feed immediately transitions gate to STALE and disarms."""
    api_state.arming_gate._is_armed = True
    assert api_state.arming_gate.is_armed is True

    res = client.post("/api/guardian/simulate", json={"scenario": "STALE_FEED"})
    assert res.status_code == 200
    data = res.json()
    assert data["applied"] is True

    # Gate fails closed
    assert api_state.arming_gate.is_armed is False
    summary = data["guardian_summary"]
    assert summary["domain_statuses"]["SYSTEM_AND_FEED"] == GuardianCheckStatus.STALE

    # Check reset restores clean state
    reset_res = client.post("/api/guardian/simulate", json={"scenario": "RESET"})
    assert reset_res.status_code == 200
    assert (
        reset_res.json()["guardian_summary"]["domain_statuses"]["SYSTEM_AND_FEED"]
        == GuardianCheckStatus.PASS
    )


def test_guardian_simulation_clock_skew(client: TestClient, api_state: APIServerState) -> None:
    """AC-005: Simulating clock skew transitions gate to FAIL and disarms."""
    api_state.arming_gate._is_armed = True

    res = client.post("/api/guardian/simulate", json={"scenario": "CLOCK_SKEW"})
    assert res.status_code == 200
    assert api_state.arming_gate.is_armed is False
    assert (
        res.json()["guardian_summary"]["domain_statuses"]["SYSTEM_AND_FEED"]
        == GuardianCheckStatus.FAIL
    )


def test_guardian_simulation_concentration_hold(client: TestClient) -> None:
    """AC-004/005: Simulating concentration hold transitions intake domain to BLOCKED."""
    res = client.post("/api/guardian/simulate", json={"scenario": "CONCENTRATION_HOLD"})
    assert res.status_code == 200
    summary = res.json()["guardian_summary"]
    assert summary["domain_statuses"]["INTAKE_AND_RISK"] == GuardianCheckStatus.BLOCKED


def test_guardian_simulation_invalid_scenario(client: TestClient) -> None:
    """AC-005: Invalid simulation scenario name returns HTTP 400."""
    res = client.post("/api/guardian/simulate", json={"scenario": "INVALID_SCENARIO_NAME"})
    assert res.status_code == 400
