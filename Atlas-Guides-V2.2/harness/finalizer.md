# Atlas Guides — Finalizer Harness

## Purpose

Validate that whole-project finalization is bounded, evidence-backed, and operationally complete.

The harness does not trust Finalizer narrative as proof.

## Preconditions

Finalization may start only when:
- every planned phase is complete;
- every required Phase Gate passed;
- no active story remains unresolved;
- a recoverable baseline exists.

## Mandatory Categories

```text
[ ] Phase completion
[ ] Finalizer scope
[ ] Repository diff
[ ] Blast radius
[ ] Dead/unused code
[ ] Dead/unused files
[ ] Dependencies
[ ] Configuration
[ ] Documentation
[ ] Full regression
[ ] Lint/format/type checks
[ ] Build/package verification
[ ] Deployment verification, if target exists
[ ] Startup verification, if target exists
[ ] Health/readiness, if target exists
[ ] Database connectivity, if applicable
[ ] Fresh initialization, if applicable
[ ] Seed/foreign-key integrity, if applicable
[ ] Critical runtime workflow, if target exists
[ ] Runtime log inspection, if target exists
```

## Deployment Applicability

Determine deployment applicability from the approved project context/specification and configured deployment target.

If a deployment target exists, deployment/runtime verification is mandatory.

If no deployment target exists, mark deployment checks `NOT_APPLICABLE` with evidence for that determination.

Do not silently omit them.

## Fresh Initialization Applicability

If startup, migration, schema creation, or seed logic is part of the application contract, fresh initialization is mandatory.

The verification environment must be disposable/staging or otherwise isolated.

Never reset production data to satisfy a harness check.

## Hard Failure Conditions

Finalizer completion is BLOCKED if any applicable check demonstrates:
- application startup failure;
- deployment crash;
- missing runtime dependency;
- health/readiness failure;
- database initialization failure;
- migration failure;
- seed failure;
- foreign-key/uniqueness integrity failure;
- critical workflow failure;
- regression caused by finalization;
- unresolved high/critical blast-radius issue;
- invalid or missing required evidence.

## Blast Radius Assessment

Every material finding must include:

```text
Finding ID:
Severity: CRITICAL | HIGH | MEDIUM | LOW
Affected surface:
Direct cause:
Potential blast radius:
Observed impact:
Evidence ID(s):
Required action:
```

Severity must reflect impact, not merely line count.

Examples:
- startup crash → CRITICAL/HIGH;
- data corruption or referential-integrity failure → CRITICAL/HIGH;
- security boundary failure → CRITICAL/HIGH;
- isolated documentation typo → LOW.

## Evidence Validation

For every execution claim:

```text
Claim → Evidence ID → Runtime Receipt → Expected → Actual → Result
```

Reject completion when evidence is missing, fabricated, stale, or unrelated to the claim.

## Artifact Validation

Finalizer must write its finalization report to the designated artifact and read it back.

The harness verifies:
- artifact exists;
- project identity is correct;
- status exists;
- evidence references exist;
- deployment/fresh-init applicability is recorded;
- blockers/findings are recorded;
- final disposition exists.

## Finalizer Output

The only acceptable finalizer statuses are:

```text
READY_TO_REVIEW
BLOCKED
```

The Finalizer does not declare `COMPLETE`.

## Finalization Gate

Finalizer output can enter Finalization Review only when:

```text
all applicable repository checks pass
AND
all applicable deployment/runtime checks pass
AND
all applicable fresh-initialization checks pass
AND
blast radius has no unresolved blocker
AND
finalization artifact is valid
```

Otherwise:

```text
FINALIZATION = BLOCKED
```
