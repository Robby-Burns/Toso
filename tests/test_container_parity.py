"""Tests for Cloud Deployment & Container Parity on Fly.io (UI-005).

Verifies Dockerfile OCI packaging, fly.toml deployment contracts, persistent volume mounts,
unauthenticated /healthz probe accessibility, zero-trust token protection, and E2E simulation.
"""

import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from toso.api.app import create_app
from toso.api.state import APIServerState
from toso.config.loader import load_config_from_file


def test_dockerfile_specification() -> None:
    """AC-001: Verify Dockerfile multi-stage packaging and single-port container contract."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root."

    content = dockerfile_path.read_text(encoding="utf-8")

    # Base image and python version
    assert "python:3.12-slim" in content

    # Environment variables
    assert "PORT=8080" in content
    assert "HOST=0.0.0.0" in content
    assert "PYTHONPATH=/app/src" in content

    # Non-root user creation
    assert "appuser" in content
    assert "USER appuser" in content

    # Static asset and config copying
    assert "COPY web/out/ /app/web/out/" in content
    assert "COPY config/ /app/config/" in content
    assert "COPY src/ /app/src/" in content

    # Single-port export
    assert "EXPOSE 8080" in content

    # Health check probe
    assert "HEALTHCHECK" in content
    assert "http://localhost:8080/healthz" in content

    # Console entrypoint
    assert 'CMD ["python", "-m", "toso", "console"]' in content


def test_fly_toml_specification() -> None:
    """AC-002/AC-003: Verify Fly.io configuration, volume mounts, and HTTP health check probe."""
    fly_path = Path("fly.toml")
    assert fly_path.exists(), "fly.toml must exist at repository root."

    with open(fly_path, "rb") as f:
        data = tomllib.load(f)

    # App and region
    assert data["app"] == "toso-trading-agent"
    assert data["primary_region"] == "sjc"

    # Environment variables
    env = data["env"]
    assert env["PORT"] == "8080"
    assert env["HOST"] == "0.0.0.0"
    assert env["TOSO_EVIDENCE_DIR"] == "/app/evidence"

    # Persistent volume mount for audit persistence (INV-001/008)
    mounts = data["mounts"]
    assert mounts["source"] == "toso_data"
    assert mounts["destination"] == "/app/evidence"

    # Compute resources
    vm = data["vm"][0]
    assert vm["memory_mb"] >= 512

    # HTTP Service configuration
    http_svc = data["http_service"]
    assert http_svc["internal_port"] == 8080
    assert http_svc["force_https"] is True
    assert http_svc["min_machines_running"] >= 1

    # Unauthenticated /healthz probe configuration
    checks = http_svc["checks"]
    assert len(checks) > 0
    healthz_check = checks[0]
    assert healthz_check["path"] == "/healthz"
    assert healthz_check["method"] == "GET"


def test_static_console_and_healthz_serving() -> None:
    """AC-001/AC-003: Verify unified single-port app serves both static UI and healthz."""
    app = create_app(static_dir="web/out")
    client = TestClient(app)

    # Health check probe
    health_res = client.get("/healthz")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"
    assert health_res.json()["service"] == "toso-api"

    # Static web UI serving
    ui_res = client.get("/")
    assert ui_res.status_code == 200
    assert "TOSO Trading Console" in ui_res.text
    assert 'id="tab-command"' in ui_res.text
    assert 'id="tab-guardian"' in ui_res.text
    assert 'id="tab-scientist"' in ui_res.text


def test_zero_trust_token_auth_and_healthz_exemption() -> None:
    """AC-004: Verify zero-trust authentication protects API while exempting /healthz."""
    token = "fly-secret-production-token-v1"
    app = create_app(auth_password=token, static_dir="web/out")
    client = TestClient(app)

    # 1. Health check probe is ALWAYS accessible without auth (Fly.io requirement)
    assert client.get("/healthz").status_code == 200

    # 2. Protected API endpoints reject unauthenticated access
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/guardian").status_code == 401
    assert client.get("/api/scientist/config").status_code == 401

    # 3. Protected API endpoints accept valid Bearer token
    headers = {"Authorization": f"Bearer {token}"}
    state_res = client.get("/api/state", headers=headers)
    assert state_res.status_code == 200
    assert state_res.json()["capital"]["total_equity"] == 100_000.00

    # 4. Protected API endpoints accept valid cookie
    cookies = {"toso_session": token}
    cookie_res = client.get("/api/guardian", cookies=cookies)
    assert cookie_res.status_code == 200


def test_e2e_container_operational_lifecycle(tmp_path: Path) -> None:
    """E2E lifecycle rehearsal: verify complete operational cycle in container mode."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    config = load_config_from_file("config/core_config_v1.yaml")
    state = APIServerState(config=config)
    app = create_app(state=state, static_dir="web/out")
    client = TestClient(app)

    # 1. Initial State Check
    state_res = client.get("/api/state")
    assert state_res.status_code == 200
    assert state_res.json()["top_bar"]["authorization"] == "DISARMED"

    # 2. Guardian Inspection
    guard_res = client.get("/api/guardian")
    assert guard_res.status_code == 200
    assert len(guard_res.json()["checks"]) > 0

    # 3. Black Swan Defense Audit
    bs_res = client.get("/api/guardian/defense")
    assert bs_res.status_code == 200
    assert bs_res.json()["allocated_pct"] == 0.05
    assert bs_res.json()["mutual_exclusivity_verified"] is True

    # 4. Scientist Studio Proposal Lifecycle
    prop_res = client.get("/api/scientist/proposals")
    assert prop_res.status_code == 200
    assert len(prop_res.json()) >= 2

    # 5. Pre-flight Check Execution
    preflight_res = client.post("/api/arming/preflight")
    assert preflight_res.status_code == 200
    assert preflight_res.json()["all_passed"] is True

    # 6. Emergency Disarm Action
    disarm_res = client.post("/api/emergency/disarm", json={"reason": "CONTAINER_E2E_TEST"})
    assert disarm_res.status_code == 200
    assert disarm_res.json()["status"] == "DISARMED"
