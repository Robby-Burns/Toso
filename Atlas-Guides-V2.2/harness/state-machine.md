# Atlas Guides — Harness State Machine

**Status:** State Transition Engine

**Authority:** Enforces unidirectional story, phase, and finalization lifecycles. Prohibits Loop 3 and unlimited finalization remediation.

## 1. Valid States

```text
READY
  ↓
BUILDING_1 → CHECKING_1
  ├─ VERIFIED → STORY_VERIFIED
  └─ FAILED/UNVERIFIED → BUILDING_2 → CHECKING_2
                                  ├─ VERIFIED → STORY_VERIFIED
                                  └─ UNRESOLVED → EVALUATION

STORY_VERIFIED
  ├─ more stories → READY
  └─ all stories → PHASE_READY → PHASE_GATE
                                  ├─ BLOCKED → BLOCKED
                                  └─ PASSED
                                      ├─ more phases → READY
                                      └─ no more phases → FINALIZING

FINALIZING
  ├─ Finalizer BLOCKED → BLOCKED
  └─ Finalizer READY_TO_REVIEW → FINALIZATION_REVIEW
                                  ├─ PASS → COMPLETE
                                  ├─ FAIL caused by Finalizer change → FINALIZATION_REMEDIATION
                                  │                                      ↓
                                  │                           FINALIZATION_REVIEW
                                  └─ FAIL/UNVERIFIED after remediation → HUMAN_REVIEW / BLOCKED
```

## 2. State Transition Matrix

| Current | Condition | Next | Owner |
|---|---|---|---|
| READY | eligible story selected | BUILDING_1 | Harness dispatch |
| BUILDING_1 | valid handoff | CHECKING_1 | Harness |
| CHECKING_1 | valid PASS | STORY_VERIFIED | Harness |
| CHECKING_1 | failed/unverified | BUILDING_2 | Harness |
| BUILDING_2 | valid handoff | CHECKING_2 | Harness |
| CHECKING_2 | valid PASS | STORY_VERIFIED | Harness |
| CHECKING_2 | unresolved | EVALUATION | Harness |
| EVALUATION | valid close | STORY_VERIFIED | Harness |
| STORY_VERIFIED | stories remain | READY | Harness |
| STORY_VERIFIED | phase complete | PHASE_READY | Harness |
| PHASE_READY | gate passes, phases remain | READY | Harness |
| PHASE_READY | gate passes, no phases remain | FINALIZING | Harness |
| PHASE_READY | gate blocked | BLOCKED | Harness |
| FINALIZING | Finalizer blocked | BLOCKED | Harness |
| FINALIZING | Finalizer READY_TO_REVIEW | FINALIZATION_REVIEW | Harness |
| FINALIZATION_REVIEW | Reviewer PASS | COMPLETE | Harness |
| FINALIZATION_REVIEW | Reviewer FAIL attributable to Finalizer | FINALIZATION_REMEDIATION | Harness |
| FINALIZATION_REVIEW | Reviewer UNVERIFIED or substantive defect | BLOCKED/HUMAN_REVIEW | Harness |
| FINALIZATION_REMEDIATION | one remediation completed | FINALIZATION_REVIEW | Harness |
| FINALIZATION_REVIEW after remediation | PASS | COMPLETE | Harness |
| FINALIZATION_REVIEW after remediation | FAIL/UNVERIFIED | BLOCKED/HUMAN_REVIEW | Harness |

## 3. Strict Invariants

- No story Loop 3.
- Checker reports verdict; harness owns story state.
- Phase Gate owns phase completion; models do not advance phases by assertion.
- Finalizer reports readiness; it does not declare `COMPLETE`.
- Finalization Reviewer reports PASS/FAIL/UNVERIFIED; it does not transition state.
- Only one Finalizer remediation cycle is permitted.
- A substantive defect outside Finalizer authority returns to engineering resolution rather than becoming cleanup work.
- Missing evidence cannot become PASS.
- Deployment/runtime verification is mandatory when a deployment target exists.
- Fresh initialization verification is mandatory when database initialization/migration/seed is part of the runtime contract.
