# Atlas Guides — Checker Harness

## Purpose

Construct a focused verification packet and validate that the Checker has produced sufficient, evidence-backed work.

The harness validates the Checker artifact. It does not depend on the Checker saying that the artifact is complete.

## Verification Packet

Provide:

```text
Story ID
Current State
Loop Number
Risk
Dependencies
Scope
Acceptance Criteria
Boundary / Rejection Conditions
Changed Files
Relevant Diff
Builder Handoff
Applicable Failure Patterns
Applicable Engineering Lessons
Project Tooling Profile
Mandatory Verification Lenses
Relevant Existing Evidence
```

## Per-Lens Contract

For every mandatory lens, require:

```text
Lens:
Question tested:
Action / command / inspection:
Evidence ID(s):
Expected:
Actual:
Result:
```

## Evidence Validation

For every Evidence ID referenced by the Checker:

1. resolve the ID in the runtime evidence store;
2. confirm the receipt exists;
3. confirm the receipt corresponds to the claimed action;
4. confirm the receipt result matches the stated Actual/Result where applicable.

If any required Evidence ID cannot be resolved:

```text
CHECKER_COMPLETION = REJECTED
```

The harness must not accept a model-authored placeholder as execution proof.

## Completion Validation

Reject Checker completion if any of the following is true:

- an AC lacks a status;
- a required AC is `UNVERIFIED` while verdict is `VERIFIED`;
- a mandatory lens lacks a complete record;
- a lens is marked VERIFIED without substantive evidence;
- an execution claim lacks Evidence ID(s);
- an Evidence ID does not resolve to a runtime receipt;
- the evidence does not support the claimed action/result;
- coverage self-audit is missing;
- findings are missing when applicable;
- verdict is missing;
- `current-loop.md` is missing required audit content;
- the Checker claims a state transition it does not own.

## Verdict Validation

The Checker may output only:

```text
VERIFIED
FAILED
UNVERIFIED
```

The harness maps that verdict to a legal transition.

## State Routing

For Loop 1:

```text
CHECKING_1 + VERIFIED
    → STORY_VERIFIED

CHECKING_1 + FAILED/UNVERIFIED
    → BUILDING_2
```

For Loop 2:

```text
CHECKING_2 + VERIFIED
    → STORY_VERIFIED

CHECKING_2 + FAILED/UNVERIFIED
    → EVALUATION
```

The harness must reject:

```text
CHECKING_1 → BUILDING_2
```

when the Checker verdict is `VERIFIED`.

The harness must reject:

```text
CHECKING_2 → BUILDING_3
```

under all circumstances.

## Important

`STORY_VERIFIED` means the story has passed its required Builder/Checker work.

It does NOT automatically mean the entire phase has passed.

If the phase contains remaining stories, continue with the next eligible story.

If all phase stories are verified, the harness routes to the Phase Gate.
