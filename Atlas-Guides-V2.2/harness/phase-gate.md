# Atlas Guides — Phase Gate Protocol

## Status
Phase acceptance protocol. Executable implementation should enforce the deterministic checks.

## Gate Conditions

### 1. Functional
- every phase story is `STORY_VERIFIED`;
- phase integration tests pass;
- required regression suite passes.

### 2. Engineering Quality
Run the commands defined by the project's tooling profile for:
- tests;
- lint;
- formatting;
- type checking;
- security;
- secret scanning;
- dependency checks.

### 3. Scope / Blast Radius
Verify:
- changed files are within declared scope or explicitly justified;
- no unexplained dependency changes;
- no suspicious artifacts;
- no temporary/debug files;
- no objectively demonstrated obsolete artifacts remain.

Do not equate "not statically referenced" with "safe to delete."

### 4. Phase Exit
Every explicit phase exit condition in `spec.md` must be satisfied.

## Output

```text
PHASE GATE REPORT
Phase:
Status: PASSED | BLOCKED

Stories:
[story → VERIFIED]

Functional:
[result]

Engineering:
[result]

Scope / Blast Radius:
[result]

Exit Conditions:
[result]

Blockers:
[list]
```

The next phase may not unlock until the gate passes.
