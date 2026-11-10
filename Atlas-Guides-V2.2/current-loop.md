# Atlas Guides — Current Story Loop

This file is a compact active-story audit record.
The runtime evidence store is authoritative for execution receipts.

## Story

```text
Story ID: LIVE-004
Title: Controlled Live Arming Protocol & Human Signoff Gate
Risk: CRITICAL
Loop: 1
State: CHECKING_1
```

## Story Scope & Acceptance Criteria
- **Scope**:
  - Implement `src/toso/live/arming.py` providing `LiveArmingGate` and `create_human_approval_token`.
  - Export new error taxonomy and models in `src/toso/live/errors.py`, `src/toso/live/models.py`, and `src/toso/live/__init__.py`.
  - Enforce non-delegable human approval protocol satisfying Specification §30 and Invariants INV-014/015.
  - Implement cryptographic signature verification sealing approver ID, timestamp, config version, matrix digest, and preflight hash.
  - Enforce prerequisite verification (100% matrix complete, 100% blocking questions resolved, 100% preflight checks passed).
  - Enforce session expiration limits (24-hour default) and instantaneous emergency disarming.
  - Deliver unit test suite `tests/test_live_arming.py` and 9-lens adversarial suite `tests/red_team/test_live_arming_adversarial.py`.
- **Acceptance Criteria**:
  - **AC-001 / Human Approval Token & Signature**: Arming without token or with corrupted/forged signature raises `InvalidApprovalTokenError`.
  - **AC-002 / Prerequisites Complete**: Missing or failing pre-flight checks, incomplete matrix, or unresolved open questions raises `LiveArmingError`.
  - **AC-003 / Digest & Version Alignment**: Mismatch between token digests and verified receipts raises `InvalidApprovalTokenError`.
  - **AC-004 / Successful Arming**: Valid token and receipts transition gate to `LIVE_ACTIVE`, set `is_armed=True`, and issue `LiveArmingReceipt` with SHA-256 seal.
  - **AC-005 / Dispatch Authorization**: `verify_order_dispatch_authorized()` succeeds without exception while system is armed and valid.
  - **AC-006 / Disarmed Dispatch Blocked**: Dispatch attempt while gate is disarmed or in `SIMULATION` raises `UnauthorizedLiveDispatchError`.
  - **AC-007 / Session Expiration**: Arming sessions past `max_session_hours` (default 24h) expire, automatically disarm gate, and raise `ExpiredArmingSessionError`.
  - **AC-008 / Immediate Disarm**: Calling `disarm_system()` immediately returns gate to `SIMULATION` mode and blocks all subsequent order dispatch.
  - **AC-009 / Runner Halt Revocation**: If runner enters emergency `HALTED` state, dispatch is blocked and gate disarms immediately with `LiveArmingError`.

## Builder 1 Delivery Record (Six-Section Handoff)

### 1. What I Built
- `src/toso/live/errors.py`: Added `UnauthorizedLiveDispatchError`, `InvalidApprovalTokenError`, and `ExpiredArmingSessionError`.
- `src/toso/live/models.py`: Added `LiveDeploymentMode`, `HumanApprovalToken`, and `LiveArmingReceipt`.
- `src/toso/live/arming.py`: Implemented `LiveArmingGate`, `compute_approval_token_signature`, and `create_human_approval_token`.
- `src/toso/live/__init__.py`: Exported all arming models, errors, and gate interface.
- `tests/test_live_arming.py`: 7 comprehensive AC tests covering AC-001 through AC-009.
- `tests/red_team/test_live_arming_adversarial.py`: 9-lens adversarial verification suite.

### 2. How I Approached It
- Strictly implemented Specification §30 approval requirements: live order dispatch is physically impossible without an explicit, cryptographically verifiable human signoff token.
- Designed three-way cryptographic verification: the token must seal the exact config version, acceptance matrix digest, and preflight receipt hash.
- Implemented stateful `LiveArmingGate` managing `SIMULATION`, `DRY_RUN`, and `LIVE_ACTIVE` modes with session expiration enforcement (24h) and runner halt revocation.

### 3. Tests Added / Run
- Unit suite: `tests/test_live_arming.py` (7/7 passed).
- Adversarial suite: `tests/red_team/test_live_arming_adversarial.py` (9/9 passed).
- Combined targeted run: `pytest tests/test_live_arming.py tests/red_team/test_live_arming_adversarial.py` (16 passed in 0.55s).
- Full regression suite: `pytest` (449 passed in 1.86s).
- Code quality: `ruff check .` (0 errors), `ruff format --check .` (163 files formatted).

### 4. Assumptions
- Human approval tokens default to a 24-hour maximum operational lifetime window.
- Fresh gate instances default to `SIMULATION` mode with `is_armed=False`.

### 5. Where to Look First
- `src/toso/live/arming.py`: `LiveArmingGate.arm_system()` and `verify_order_dispatch_authorized()`.
- `tests/test_live_arming.py`: AC-001 to AC-009 verification tests.
- `tests/red_team/test_live_arming_adversarial.py`: 9-lens adversarial suite.

### 6. Open Questions / Unresolved Risks
- None. Live arming gate is fully deterministic, fail-closed, and verified against all 9 lenses.
