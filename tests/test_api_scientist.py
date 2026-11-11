"""Tests for Scientist Studio & Proposal Diff Workbench (UI-004).

Verifies active config inspection, research proposal lifecycle, backtest metric diffs,
cryptographic approval gates (INV-014), and atomic production adoption (INV-015).
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


def test_get_scientist_config(client: TestClient) -> None:
    """AC-001: Verify active config inspection and metadata."""
    res = client.get("/api/scientist/config")
    assert res.status_code == 200
    data = res.json()

    assert data["version"] == "1.0.0"
    assert data["status"] == "APPROVED"
    assert "strategy:" in data["raw_yaml"]
    assert data["strategy_params"]["pyramid_min_profit_r"] == 1.0
    assert data["strategy_params"]["breakout_lookback_bars"] == 20


def test_list_and_get_proposals(client: TestClient) -> None:
    """AC-002/AC-003/AC-004: Verify candidate proposals, metric diffs, and YAML diffs."""
    res = client.get("/api/scientist/proposals")
    assert res.status_code == 200
    props = res.json()
    assert len(props) >= 2

    # Verify PROP-2026-001 details
    p1 = next(p for p in props if p["proposal_id"] == "PROP-2026-001")
    assert p1["hypothesis_id"] == "HYP-VOL-PYRAMID-01"
    assert p1["status"] == "SUBMITTED_FOR_REVIEW"
    assert p1["is_approved"] is False

    # Backtest metric diffs
    assert p1["baseline_metrics"]["sharpe_ratio"] == 1.45
    assert p1["proposed_metrics"]["sharpe_ratio"] == 1.82
    assert p1["proposed_metrics"]["calmar_ratio"] > p1["baseline_metrics"]["calmar_ratio"]
    assert p1["proposed_metrics"]["max_drawdown_pct"] < p1["baseline_metrics"]["max_drawdown_pct"]

    # Equity curves
    assert len(p1["equity_curve_baseline"]) == 20
    assert len(p1["equity_curve_proposed"]) == 20

    # YAML diff string
    assert "--- config/core_config_v1.yaml" in p1["yaml_diff"]
    assert "+  pyramid_min_profit_r: 1.2" in p1["yaml_diff"]

    # Single proposal lookup
    single_res = client.get("/api/scientist/proposals/PROP-2026-001")
    assert single_res.status_code == 200
    assert single_res.json()["proposal_id"] == "PROP-2026-001"


def test_create_and_review_proposal_workflow(client: TestClient) -> None:
    """AC-002: Formulate new proposal and transition through review."""
    payload = {
        "proposal_id": "PROP-TEST-999",
        "hypothesis_id": "HYP-TEST-FAST-MA",
        "rationale": "Test fast MA lookback reduction to 40 bars.",
        "proposed_parameters": {"fast_ma_period": 40},
    }
    create_res = client.post("/api/scientist/proposals", json=payload)
    assert create_res.status_code == 200
    created = create_res.json()
    assert created["proposal_id"] == "PROP-TEST-999"
    assert created["status"] == "DRAFT"

    # Transition to SUBMITTED_FOR_REVIEW
    review_res = client.post(
        "/api/scientist/proposals/PROP-TEST-999/review",
        json={"action": "SUBMIT", "reason": "Ready for operator signoff."},
    )
    assert review_res.status_code == 200
    assert review_res.json()["proposal"]["status"] == "SUBMITTED_FOR_REVIEW"


def test_cryptographic_approval_gate(client: TestClient) -> None:
    """AC-005 (INV-014): Cryptographic approval requires valid salt and approver."""
    # Attempt with wrong salt
    bad_res = client.post(
        "/api/scientist/proposals/PROP-2026-001/approve",
        json={
            "approver_id": "operator-burns",
            "secret_salt": "WRONG-SALT",
            "validity_minutes": 60,
        },
    )
    assert bad_res.status_code == 403

    # Attempt with empty approver
    empty_res = client.post(
        "/api/scientist/proposals/PROP-2026-001/approve",
        json={
            "approver_id": "",
            "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1",
            "validity_minutes": 60,
        },
    )
    assert empty_res.status_code == 403

    # Valid approval
    ok_res = client.post(
        "/api/scientist/proposals/PROP-2026-001/approve",
        json={
            "approver_id": "operator-burns",
            "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1",
            "validity_minutes": 60,
        },
    )
    assert ok_res.status_code == 200
    data = ok_res.json()
    assert data["proposal"]["status"] == "APPROVED"
    assert data["proposal"]["is_approved"] is True
    assert data["proposal"]["approver_id"] == "operator-burns"
    assert "RCPT-PROP" in data["receipt_id"]
    assert len(data["checksum"]) == 64


def test_unauthorized_adoption_rejected(client: TestClient) -> None:
    """AC-005 (INV-014/015): Unapproved proposals cannot be adopted into production."""
    # PROP-2026-002 is in DRAFT
    adopt_res = client.post(
        "/api/scientist/proposals/PROP-2026-002/adopt",
        json={
            "new_version": "1.1.0",
            "approver_id": "operator-burns",
            "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1",
        },
    )
    assert adopt_res.status_code == 403


def test_atomic_proposal_adoption_to_production(client: TestClient) -> None:
    """AC-005 (INV-015): Full lifecycle from approval to atomic CoreConfig adoption."""
    # 1. Approve proposal
    appr_res = client.post(
        "/api/scientist/proposals/PROP-2026-001/approve",
        json={
            "approver_id": "lead-quant-burns",
            "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1",
            "validity_minutes": 60,
        },
    )
    assert appr_res.status_code == 200

    # 2. Adopt proposal
    adopt_res = client.post(
        "/api/scientist/proposals/PROP-2026-001/adopt",
        json={
            "new_version": "1.1.0",
            "approver_id": "lead-quant-burns",
            "secret_salt": "TOSO-LIVE-ARMING-AUTH-v1",
        },
    )
    assert adopt_res.status_code == 200
    data = adopt_res.json()
    assert data["proposal"]["status"] == "ADOPTED"

    # 3. Verify active config is updated atomically
    cfg_res = client.get("/api/scientist/config")
    assert cfg_res.status_code == 200
    cfg_data = cfg_res.json()
    assert cfg_data["version"] == "1.1.0"
    assert cfg_data["approved_by"] == "lead-quant-burns"
    assert cfg_data["strategy_params"]["pyramid_min_profit_r"] == 1.2
    assert cfg_data["strategy_params"]["breakout_lookback_bars"] == 25
