# Atlas Guides — Build Plan: TOSO — Bear & Bull Trading

## Status
- Active Phase: Complete (All Phases 1–7 Complete)
- Active Story: None (All Stories Verified)
- Build State: FINALIZATION_READY

---

## 1. Project Phases & Story Sequence

### Phase 1 — Deterministic Trader
**Objective:** Establish deterministic Core trading behavior, entry invalidations, position sizing, winner-only pyramiding, and winner continuation.
**Exit Conditions:**
- Core behavior is deterministic.
- No averaging down is technically possible.
- Invalidations are strictly enforced without discretionary override.
- Winner continuation is enforced (no premature taking of valid trends).
- Pyramiding is winner-only and evaluates aggregate position/portfolio risk.
- Gap/slippage risk is explicitly incorporated into risk calculations.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **TRADER-001** | Deterministic Core Configuration | HIGH | None | **VERIFIED** |
| **TRADER-002** | Deterministic Signal Generation | HIGH | TRADER-001 | **VERIFIED** |
| **TRADER-003** | Entry & Invalidation Definition | CRITICAL | TRADER-001, TRADER-002 | **VERIFIED** |
| **TRADER-004** | Invalidation Exit Enforcement | CRITICAL | TRADER-003 | **VERIFIED** |
| **TRADER-005** | Position Sizing & Gap/Slippage Accounting | HIGH | TRADER-001, TRADER-003 | **VERIFIED** |
| **TRADER-006** | Winner-Only Pyramiding & Aggregate Risk | HIGH | TRADER-004, TRADER-005 | **VERIFIED** |
| **TRADER-007** | Winner Continuation & Anti-Take-Profit Rules | HIGH | TRADER-006 | **VERIFIED** |

---

### Phase 2 — Guardian
**Objective:** Build the deterministic safety boundary around the Trader.
**Exit Conditions:**
- No order bypasses the Execution Gate.
- UNKNOWN inputs fail closed (block/hold).
- Required protection contracts verified before adding exposure.
- Capital separation (Core vs. Reserve vs. Black Swan) is enforced.
- Emergency controls tested and operational.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **GUARD-001** | Portfolio Risk Controls | HIGH | Phase 1 | **VERIFIED** |
| **GUARD-002** | Concentration Intake Gating | HIGH | GUARD-001 | **VERIFIED** |
| **GUARD-003** | Capital Separation & Reserve Isolation | CRITICAL | GUARD-001 | **VERIFIED** |
| **GUARD-004** | Execution Gate (Sole Authority) | CRITICAL | GUARD-001, GUARD-002, GUARD-003 | **VERIFIED** |
| **GUARD-005** | Protection Contract & Recovery | CRITICAL | GUARD-004 | **VERIFIED** |
| **GUARD-006** | Emergency Stop & Fail-Closed Behavior | CRITICAL | GUARD-004, GUARD-005 | **VERIFIED** |

---

### Phase 3 — Broker Integrity
**Objective:** Make broker state authoritative, reconciled, and resilient against ambiguity.
**Exit Conditions:**
- Broker state can be reconciled against local state.
- Ambiguous order states cannot cause blind duplicate exposure.
- Idempotent order identity prevents double-submission.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **BROKER-001** | Account Reconciliation | CRITICAL | Phase 2 | **VERIFIED** |
| **BROKER-002** | Order Reconciliation & Ambiguity Handling | CRITICAL | BROKER-001 | **VERIFIED** |
| **BROKER-003** | Position Reconciliation | CRITICAL | BROKER-001, BROKER-002 | **VERIFIED** |
| **BROKER-004** | Ambiguous Outcome Handling & No Blind Retry | CRITICAL | BROKER-002 | **VERIFIED** |
| **BROKER-005** | Idempotent Order Identity & Submission | CRITICAL | BROKER-004 | **VERIFIED** |

---

### Phase 4 — Black Swan Defense
**Objective:** Implement independent portfolio defense against tail-risk events.
**Exit Conditions:**
- Black Swan defense remains independent of Core Trader.
- Defensive behavior is deterministic.
- Capital is separately allocated and controlled.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **BS-001** | Black Swan Capital Allocation & Separation | HIGH | Phase 3 | **VERIFIED** |
| **BS-002** | Defensive Instruments & Universe Configuration | HIGH | BS-001 | **VERIFIED** |
| **BS-003** | Defensive Sizing & Trigger Logic | HIGH | BS-002 | **VERIFIED** |
| **BS-004** | Defensive Exits & De-escalation | HIGH | BS-003 | **VERIFIED** |

---

### Phase 5 — Scientist
**Objective:** Create the research and journaling layer with strict isolation from execution.
**Exit Conditions:**
- Scientist tools cannot submit or cancel production orders.
- Append-only journaling of trades and observations.
- Research, hypothesis, and backtest frameworks are reproducible.
- Production modifications require human approval.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **SCI-001** | Observation & Append-Only Trade Journaling | HIGH | Phase 3 | **VERIFIED** |
| **SCI-002** | Scientist Execution Boundary & Security Isolation | CRITICAL | SCI-001 | **VERIFIED** |
| **SCI-003** | Hypothesis Definition & Storage | MEDIUM | SCI-001 | **VERIFIED** |
| **SCI-004** | Experiment Design & Parameterization | MEDIUM | SCI-003 | **VERIFIED** |
| **SCI-005** | Backtesting Engine | HIGH | SCI-004 | **VERIFIED** |
| **SCI-006** | Out-of-Sample (OOS) Evaluation | HIGH | SCI-005 | **VERIFIED** |
| **SCI-007** | Production Proposal & Human Approval Workflow | CRITICAL | SCI-002, SCI-006 | **VERIFIED** |

---

### Phase 6 — Paper Validation
**Objective:** Validate complete system behavior in a live simulation environment without capital risk.
**Exit Conditions:**
- Core behavior verified.
- Guardian verified.
- Broker integration verified.
- Research boundary verified.
- Failure paths tested.
- Journal verified.
- Emergency behavior verified.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **PAPER-001** | Paper Broker & Environment Engine | HIGH | Phase 5 | **VERIFIED** |
| **PAPER-002** | End-to-End Core Trader & Guardian Integration | CRITICAL | PAPER-001 | **VERIFIED** |
| **PAPER-003** | Black Swan Defense in Paper Simulation | HIGH | PAPER-002 | **VERIFIED** |
| **PAPER-004** | Journaling & Scientist Boundary in Paper Mode | HIGH | PAPER-002 | **VERIFIED** |
| **PAPER-005** | Failure Injections & Emergency Kill-Switch | CRITICAL | PAPER-002, PAPER-004 | **VERIFIED** |

---

### Phase 7 — Live Readiness
**Objective:** Demonstrate operational readiness, safety boundary verification, and human signoff.
**Exit Conditions:**
- All blocking Open Questions resolved.
- All required stories verified.
- Acceptance Matrix complete.
- No unresolved critical safety findings.
- Human approval obtained.

| Story ID | Title | Risk | Dependencies | Status |
|---|---|---|---|---|
| **LIVE-001** | Resolution of Blocking Open Questions & Parameter Locking | HIGH | Phase 6 | **VERIFIED** |
| **LIVE-002** | Acceptance Matrix Audit & Traceability | HIGH | LIVE-001 | **VERIFIED** |
| **LIVE-003** | Operational Pre-Flight Checks & Safety Engine | CRITICAL | LIVE-001 | **VERIFIED** |
| **LIVE-004** | Controlled Live Arming Protocol & Human Signoff Gate | CRITICAL | LIVE-002, LIVE-003 | **VERIFIED** |

---

## 2. Execution Governance Rules
- Every story has at most two Builder/Checker passes (Builder 1 → Checker 1 → Builder 2 → Checker 2).
- There is no Loop 3.
- Only an unresolved Checker 2 result routes to Evaluator.
- Phase completion requires satisfying the Phase Gate.
