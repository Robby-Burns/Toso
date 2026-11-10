# Atlas Guides — Finalization Reviewer Harness

## Purpose

Validate that the Finalization Reviewer independently verified the Finalizer's work and that final acceptance is supported by repository, deployment, runtime, and evidence checks.

The Reviewer does not own workflow state.

## Inputs

- completed project;
- approved specification;
- completed Phase Gate results;
- Finalization baseline;
- Finalizer report and diff;
- repository status;
- shared evidence store;
- configured verification commands;
- configured deployment target, if any;
- finalization review artifact.

## Mandatory Review Coverage

```text
[ ] Finalizer diff
[ ] Added files
[ ] Modified files
[ ] Deleted files
[ ] Renamed files
[ ] Dependency changes
[ ] Configuration changes
[ ] Documentation changes
[ ] Test changes
[ ] Blast radius
[ ] Dead/unused code
[ ] Dead/unused files
[ ] Temporary/debug artifacts
[ ] Stale references
[ ] Engineering standards
[ ] Regression
[ ] Product invariants
[ ] Deployment/runtime, if target exists
[ ] Startup/lifespan, if target exists
[ ] Health/readiness, if target exists
[ ] Database connectivity, if applicable
[ ] Fresh initialization, if applicable
[ ] Seed/foreign-key integrity, if applicable
[ ] Critical runtime workflow, if target exists
[ ] Runtime log inspection, if target exists
```

## Deployment Applicability

Determine whether a deployment target exists from approved project context/configuration.

If yes, runtime verification is mandatory.

If no, record `NOT_APPLICABLE` and evidence for that determination.

Do not silently skip deployment review.

## Fresh Initialization Applicability

If database initialization, migrations, schema creation, or seed logic is part of the runtime contract, fresh initialization is mandatory.

Use an isolated/disposable environment. Never reset production data to satisfy the harness.

## Hard PASS Requirements

Reviewer PASS requires:

```text
all applicable repository checks pass
AND
all applicable deployment/runtime checks pass
AND
all applicable fresh-initialization checks pass
AND
critical workflow passes
AND
no material blocker remains
AND
blast radius is assessed
AND
required evidence is valid
AND
review artifact is complete
```

A local `pytest` pass is not sufficient when deployment/runtime verification is applicable.

## Hard Failure Conditions

Reject PASS when there is evidence of:
- deployment crash;
- startup/lifespan exception;
- missing production runtime dependency;
- health/readiness failure;
- database connection failure;
- migration/schema failure;
- seed failure;
- foreign-key/uniqueness integrity failure;
- critical workflow failure;
- regression caused by Finalizer;
- unsafe deletion;
- material scope violation;
- unresolved high/critical blast-radius issue.

## Evidence Validation

Every execution claim must follow:

```text
Claim → Evidence ID → Runtime Receipt → Expected → Actual → Result
```

Reject completion if evidence is missing, fabricated, stale, or unrelated.

## Artifact Validation

The Reviewer must write and read back the final review artifact.

The harness validates:
- artifact exists;
- correct project identity;
- verdict present;
- findings present;
- evidence references present;
- deployment applicability recorded;
- fresh-init applicability recorded;
- completion checklist present.

A model statement that the artifact was updated is not sufficient.

## Verdicts

Canonical Reviewer verdicts:

```text
PASS
FAIL
UNVERIFIED
```

Reject ambiguous verdicts.

## Blast Radius Requirement

Every material finding must include:

```text
Severity
Affected surface
Potential blast radius
Observed impact
Evidence
```

Severity reflects impact, not implementation size.

## State Routing

The Reviewer reports a verdict. The harness applies state.

```text
FINALIZATION_REVIEW + PASS
    → COMPLETE

FINALIZATION_REVIEW + FAIL
    → FINALIZATION_REMEDIATION

FINALIZATION_REMEDIATION
    → FINALIZATION_REVIEW
```

## Remediation Boundary

Only one remediation cycle is allowed, and only when the finding is attributable to the Finalizer's permitted changes.

A substantive pre-existing engineering defect is not a Finalizer cleanup remediation. It returns the project to `BLOCKED` / engineering resolution.

After the one permitted remediation:
- PASS → COMPLETE;
- FAIL → human decision;
- UNVERIFIED → human decision.

No unlimited loop.
