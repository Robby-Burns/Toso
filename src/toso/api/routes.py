"""TOSO FastAPI HTTP and WebSocket Routes.

Exposes endpoints for system state, Guardian audit, emergency disarm,
preflight checks, and live arming.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from toso.api.models import (
    AdoptProposalRequest,
    ApproveProposalRequest,
    ArmingRequest,
    ArmingResponse,
    BlackSwanDefenseResponse,
    CreateProposalRequest,
    EmergencyDisarmRequest,
    EmergencyDisarmResponse,
    GuardianSummaryResponse,
    PreflightResponse,
    ProposalActionResponse,
    ProposalItem,
    ReviewProposalRequest,
    ScientistConfigResponse,
    SimulationRequest,
    SimulationResponse,
    StateSyncResponse,
    SystemStateResponse,
)
from toso.api.state import APIServerState
from toso.scientist.errors import (
    InvalidApprovalTokenError,
    InvalidProposalStateTransitionError,
    ProposalNotFoundError,
    UnauthorizedProductionChangeError,
)

router = APIRouter()


def get_state(request: Request) -> APIServerState:
    """Dependency retrieving application server state from app instance."""
    return request.app.state.server_state  # type: ignore[no-any-return]


@router.get("/healthz", tags=["Health"])
async def health_check() -> dict[str, Any]:
    """Lightweight health check for Fly.io and container orchestrators."""
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "toso-api",
    }


@router.get("/api/state", response_model=SystemStateResponse, tags=["State"])
async def get_system_state(state: APIServerState = Depends(get_state)) -> SystemStateResponse:
    """Retrieve full consolidated system state."""
    return state.get_system_state()


@router.get("/api/state/sync", response_model=StateSyncResponse, tags=["State"])
async def get_state_sync(
    since_seq: int = Query(default=0, ge=0),
    state: APIServerState = Depends(get_state),
) -> StateSyncResponse:
    """Resynchronize missed events after a client reconnection."""
    missed = state.ws_hub.get_events_since(since_seq)
    return StateSyncResponse(
        current_sequence_id=state.ws_hub.current_sequence_id,
        state=state.get_system_state(),
        missed_events=missed,
    )


@router.get("/api/guardian", response_model=GuardianSummaryResponse, tags=["Guardian"])
async def get_guardian_summary(
    state: APIServerState = Depends(get_state),
) -> GuardianSummaryResponse:
    """Retrieve 5-state Guardian health check across all three domains."""
    return state.get_guardian_summary()


@router.get("/api/guardian/defense", response_model=BlackSwanDefenseResponse, tags=["Guardian"])
async def get_black_swan_defense(
    state: APIServerState = Depends(get_state),
) -> BlackSwanDefenseResponse:
    """Retrieve Black Swan ring-fenced capital allocation and defensive universe status."""
    return state.get_black_swan_defense()


@router.post("/api/guardian/simulate", response_model=SimulationResponse, tags=["Guardian"])
async def simulate_guardian_scenario(
    req: SimulationRequest,
    state: APIServerState = Depends(get_state),
) -> SimulationResponse:
    """Simulate an operational failure scenario to test fail-closed behavior."""
    try:
        return await state.apply_simulation(req.scenario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/emergency/disarm", response_model=EmergencyDisarmResponse, tags=["Safety"])
async def emergency_disarm(
    req: EmergencyDisarmRequest = EmergencyDisarmRequest(),
    state: APIServerState = Depends(get_state),
) -> EmergencyDisarmResponse:
    """Instant single-action emergency stop and disarm."""
    return await state.execute_emergency_disarm(reason=req.reason)


@router.post("/api/arming/preflight", response_model=PreflightResponse, tags=["Arming"])
async def run_preflight(state: APIServerState = Depends(get_state)) -> PreflightResponse:
    """Execute pre-flight check suite prior to live arming."""
    receipt = state.run_preflight_checks()
    return PreflightResponse(
        receipt_id=receipt.receipt_id,
        all_passed=receipt.all_passed,
        receipt_hash=receipt.receipt_hash,
        checks=[c.model_dump(mode="json") for c in receipt.checks],
    )


@router.post("/api/arming/arm", response_model=ArmingResponse, tags=["Arming"])
async def arm_system(
    req: ArmingRequest,
    state: APIServerState = Depends(get_state),
) -> ArmingResponse:
    """Verify pre-flight, acceptance matrix, and cryptographically seal live arming."""
    try:
        receipt = await state.execute_live_arming(
            approver_id=req.approver_id,
            secret_salt=req.secret_salt,
            notes=req.notes,
        )
        return ArmingResponse(
            success=True,
            arming_id=receipt.arming_id,
            mode=receipt.mode.value,
            armed_at=receipt.armed_at,
            arming_digest=receipt.arming_digest,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/config", tags=["Config"])
async def get_active_config(state: APIServerState = Depends(get_state)) -> dict[str, Any]:
    """Retrieve active configuration data."""
    return state.config.model_dump(mode="json")


# =========================================================================
# Scientist Studio & Proposal Diff Workbench (UI-004)
# =========================================================================


@router.get(
    "/api/scientist/config",
    response_model=ScientistConfigResponse,
    tags=["Scientist"],
)
async def get_scientist_config(
    state: APIServerState = Depends(get_state),
) -> ScientistConfigResponse:
    """Retrieve active CoreConfig YAML and metadata for Scientist Studio."""
    return state.get_scientist_config()


@router.get(
    "/api/scientist/proposals",
    response_model=list[ProposalItem],
    tags=["Scientist"],
)
async def list_proposals(
    state: APIServerState = Depends(get_state),
) -> list[ProposalItem]:
    """List all research proposals with backtest metrics and diffs."""
    return state.get_proposals()


@router.get(
    "/api/scientist/proposals/{proposal_id}",
    response_model=ProposalItem,
    tags=["Scientist"],
)
async def get_proposal_details(
    proposal_id: str,
    state: APIServerState = Depends(get_state),
) -> ProposalItem:
    """Retrieve a single research proposal by ID."""
    try:
        return state.get_proposal(proposal_id)
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post(
    "/api/scientist/proposals",
    response_model=ProposalItem,
    tags=["Scientist"],
)
async def create_new_proposal(
    req: CreateProposalRequest,
    state: APIServerState = Depends(get_state),
) -> ProposalItem:
    """Formulate a new research proposal."""
    try:
        return await state.create_proposal(req)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/scientist/proposals/{proposal_id}/review",
    response_model=ProposalActionResponse,
    tags=["Scientist"],
)
async def review_proposal_status(
    proposal_id: str,
    req: ReviewProposalRequest,
    state: APIServerState = Depends(get_state),
) -> ProposalActionResponse:
    """Transition proposal lifecycle state (SUBMIT | REJECT | WITHDRAW)."""
    try:
        return await state.review_proposal(
            proposal_id=proposal_id, action=req.action, reason=req.reason
        )
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (InvalidProposalStateTransitionError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/scientist/proposals/{proposal_id}/approve",
    response_model=ProposalActionResponse,
    tags=["Scientist"],
)
async def approve_proposal(
    proposal_id: str,
    req: ApproveProposalRequest,
    state: APIServerState = Depends(get_state),
) -> ProposalActionResponse:
    """Cryptographically approve a research proposal with a HumanApprovalToken (INV-014)."""
    try:
        return await state.approve_proposal(
            proposal_id=proposal_id,
            approver_id=req.approver_id,
            secret_salt=req.secret_salt,
            validity_minutes=req.validity_minutes,
        )
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidApprovalTokenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except InvalidProposalStateTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/scientist/proposals/{proposal_id}/adopt",
    response_model=ProposalActionResponse,
    tags=["Scientist"],
)
async def adopt_proposal(
    proposal_id: str,
    req: AdoptProposalRequest,
    state: APIServerState = Depends(get_state),
) -> ProposalActionResponse:
    """Atomically adopt an approved research proposal into active production config (INV-015)."""
    try:
        return await state.adopt_proposal(
            proposal_id=proposal_id,
            new_version=req.new_version,
            approver_id=req.approver_id,
            secret_salt=req.secret_salt,
        )
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (UnauthorizedProductionChangeError, InvalidApprovalTokenError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
) -> None:
    """Client WebSocket endpoint for real-time sequenced telemetry."""
    state: APIServerState = websocket.app.state.server_state
    await state.ws_hub.connect(websocket)
    try:
        # Send initial snapshot upon connection
        snapshot = state.get_system_state().model_dump(mode="json")
        await websocket.send_json(
            {
                "event_type": "INITIAL_STATE",
                "sequence_id": state.ws_hub.current_sequence_id,
                "payload": snapshot,
            }
        )
        while True:
            # Maintain active connection, handle client pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await state.ws_hub.disconnect(websocket)
    except Exception:
        await state.ws_hub.disconnect(websocket)
