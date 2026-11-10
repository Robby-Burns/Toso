# Atlas Guides — Canonical State Machine

**Status:** Definitive Routing Logic
**Authority:** The single source of truth for all lifecycle state transitions, gate routing, and evaluator remediation flows.

## 1. Story Lifecycle (Execution Loop)

```text
READY
  ↓
BUILDING_1
  ↓
CHECKING_1
  ├─ VERIFIED → STORY_VERIFIED
  └─ FAILED/UNVERIFIED → BUILDING_2

BUILDING_2
  ↓
CHECKING_2
  ├─ VERIFIED → STORY_VERIFIED
  └─ FAILED/UNVERIFIED → EVALUATION
```

## 2. Evaluator Routing

```text
EVALUATION
  ├─ BUILDER_VALID
  │    └─ → STORY_VERIFIED
  │
  ├─ CHECKER_VALID
  │    └─ → BLOCKED (Awaiting human remediation story entry in build-plan.md)
  │
  ├─ REWORK_REQUIRED
  │    └─ → BLOCKED (Awaiting human Spec/Plan restructure & approval)
  │
  └─ SPEC_DEFECT
       └─ → BLOCKED (Awaiting human Spec amendment & approval)
```

*Note: There is no autonomous Loop 3. Any new work generated from Evaluator findings must re-enter the standard lifecycle via an approved `build-plan.md` addition.*

## 3. Phase Lifecycle

```text
PHASE_READY
  ↓
PHASE_EXECUTING (Stories loop here until all are STORY_VERIFIED)
  ↓
PHASE_GATE
  ├─ BLOCKED → Halt / Human Review
  └─ PASSED
       ├─ Next phase exists → PHASE_READY
       └─ All phases complete → FINALIZATION_READY
```

## 4. Finalization & Review Lifecycle

```text
FINALIZATION_READY
  ↓
FINALIZATION_BASELINE (Create recoverable checkpoint)
  ↓
FINALIZING
  ↓
FINALIZER_READINESS_GATE
  ├─ BLOCKED → Halt / Rollback
  └─ READY_TO_REVIEW
       ↓
FINALIZATION_REVIEW
  ├─ PASS
  │    └─ → FINALIZATION_GATE → COMPLETE
  │
  ├─ FAIL (Finalizer cause)
  │    └─ → FINALIZATION_REMEDIATION (One bounded pass only) → FINALIZATION_REVIEW
  │
  └─ FAIL/UNVERIFIED (Substantive pre-existing defect or 2nd fail)
       └─ → BLOCKED / HUMAN_REVIEW
```
