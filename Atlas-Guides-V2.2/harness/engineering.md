# Atlas Guides — Engineering Harness Orchestrator

**Status:** Orchestration Engine

**Authority:** Governs story lifecycle, phase gates, finalization, finalization review, and bounded remediation.

## 1. Responsibilities

The orchestrator:
- selects eligible stories from `build-plan.md`;
- dispatches configured Builder, Checker, Evaluator, Phase Gate, Finalizer, and Finalization Reviewer roles;
- enforces the two-pass story cap;
- gathers and validates evidence;
- evaluates Phase Gates deterministically;
- triggers whole-build Finalization only after all phases pass;
- triggers independent Finalization Review after Finalizer readiness;
- enforces one Finalizer remediation cycle;
- blocks completion when required runtime/deployment evidence is absent or failed.

## 2. Global Workflow

```text
STORIES
  ↓
Builder 1 → Checker 1
  ├─ PASS → Story Verified
  └─ FAIL → Builder 2 → Checker 2
                         ├─ PASS → Story Verified
                         └─ unresolved → Evaluator

Story Verified
  ↓
Phase Gate
  ├─ BLOCKED → Halt
  └─ PASSED
       ├─ more phases → Next Phase
       └─ no phases → Finalization

Finalization
  ↓
Finalizer
  ├─ BLOCKED → Halt
  └─ READY_TO_REVIEW
       ↓
Finalization Reviewer
  ├─ PASS → COMPLETE
  ├─ FAIL caused by Finalizer → One Finalizer remediation → Reviewer
  └─ FAIL/UNVERIFIED after remediation or substantive defect → HUMAN/BLOCKED
```

## 3. Dispatch Rules

1. Model/provider assignments come from runtime configuration, never from role documents.
2. Finalizer receives whole-project context only after all Phase Gates pass.
3. Finalization Reviewer receives the Finalizer diff, report, baseline, evidence, deployment context, and final project artifacts.
4. Reviewer must independently verify deployment/runtime behavior when a deployment target exists.
5. Reviewer must independently verify fresh initialization when database initialization/migration/seed is part of the runtime contract.
6. The orchestrator must not infer runtime success from local tests alone.
7. The orchestrator must validate finalization artifacts before allowing the Reviewer result to affect state.
8. No unlimited Finalizer ↔ Reviewer loop.

## 4. Finalization Routing

```text
FINALIZING
    ↓
Finalizer
    ↓
Finalizer Harness
    ├─ BLOCKED → BLOCKED
    └─ READY_TO_REVIEW
          ↓
Finalization Reviewer
          ├─ PASS → COMPLETE
          ├─ FAIL + Finalizer-attributable → FINALIZATION_REMEDIATION
          │                                      ↓
          │                               Finalizer
          │                                      ↓
          │                               Reviewer again
          └─ FAIL/UNVERIFIED + substantive or second failure → BLOCKED/HUMAN_REVIEW
```

## 5. Runtime Gate Principle

When the project declares a deployment target, finalization cannot pass solely on repository checks.

Required runtime evidence includes the applicable:
- deployment success;
- startup/lifespan success;
- health/readiness;
- runtime dependency loading;
- database connectivity;
- fresh initialization;
- seed/reference integrity;
- critical user workflow;
- runtime log inspection.

## 6. Blast Radius Principle

Any issue found during finalization or review must be evaluated for its potential system-wide impact.

The orchestrator should treat startup failure, data-integrity failure, security failure, and primary-workflow failure as blocking regardless of how small the source diff appears.

## 7. No Self-Certification

No model may certify its own work as complete solely from its narrative.

```text
Finalizer claim
   ↓
Finalizer evidence/artifact
   ↓
Independent Reviewer
   ↓
Harness validation
   ↓
State transition
```
