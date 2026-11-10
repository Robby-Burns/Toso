# Atlas Guides — Finalizer Directive

## Mission

Perform one bounded, whole-project finalization pass after all phases have passed their gates.

The objective is not to add features. The objective is to clean, verify, and prove that the completed project remains operational.

## Required Reading

Before acting:
1. Read `kernel.md`.
2. Read `build-plan.md`.
3. Read the approved `spec.md`.
4. Read `.build-context.md`.
5. Read all relevant Phase Gate reports.
6. Read `coding-standards.md`.
7. Read the Finalizer role.
8. Read the configured verification/deployment instructions.
9. Inspect repository status and the final baseline.

## Sequence

### 1. Establish Baseline

Record:
- current commit/state;
- all completed phases;
- deployment target, if any;
- configured verification commands;
- finalization scope.

Create or confirm a recoverable baseline before modifying the repository.

### 2. Inspect Whole Repository

Review:
- source tree;
- tests;
- configuration;
- dependencies;
- generated artifacts;
- documentation;
- deployment files;
- database initialization/migration/seed code;
- entry points and startup lifecycle.

### 3. Assess Blast Radius

For each proposed cleanup, identify:
- directly affected files;
- shared dependencies;
- runtime/startup impact;
- data/persistence impact;
- API/integration impact;
- user-visible impact;
- deployment impact.

Do not delete a file merely because a simple text search finds no reference. Check dynamic imports, configuration references, packaging, startup registration, migrations, scripts, and deployment behavior where relevant.

### 4. Perform Safe Cleanup

Apply only changes within Finalizer authority.

Do not convert substantive defects into cleanup changes.

### 5. Run Repository Verification

Run the project's configured:
- full test suite;
- lint;
- formatter check;
- type checks;
- build/package checks;
- security/secret checks where configured.

### 6. Verify Deployment Runtime

If a deployment target exists:

1. Build the intended deployment artifact.
2. Deploy using the project's configured process.
3. Confirm deployment succeeds.
4. Inspect startup logs.
5. Confirm the application reaches a healthy/readiness state.
6. Verify runtime dependencies load.
7. Verify required configuration is present without exposing secrets.
8. Verify database connectivity.
9. Verify schema/migration initialization.
10. Verify the critical user workflow.
11. Inspect logs after the workflow.

A deployment crash, startup exception, missing runtime dependency, or failed critical workflow is a blocking finalization issue.

### 7. Verify Fresh Initialization

If database initialization/migration/seed is part of the application contract:

1. Use a disposable or staging database/environment.
2. Start from a clean state.
3. Run migrations/schema creation.
4. Run required seed/initialization logic.
5. Verify foreign-key and uniqueness constraints.
6. Verify required records exist.
7. Start the application against that clean environment.
8. Exercise the critical path.

Do not use production data for destructive reset testing.

### 8. Recheck After Cleanup

Run the relevant verification again after all cleanup and deployment changes.

### 9. Documentation Synchronization

Ensure documentation describes the actual final system. Do not rewrite product decisions simply to make the documentation look cleaner.

### 10. Finalizer Report

Write the report to the designated finalization artifact and read it back.

Use:

```markdown
# Finalizer System Audit Report

Project: [Project]
Finalization Status: READY_TO_REVIEW | BLOCKED

## 1. Cleanup
- ...

## 2. Blast Radius
- Highest-risk cleanup:
- Affected surfaces:
- Why safe:

## 3. Repository Verification
- Tests:
- Lint:
- Format:
- Type checks:
- Build:

## 4. Deployment Verification
- Target:
- Deployment:
- Startup:
- Health/readiness:
- Runtime dependencies:
- Critical workflow:
- Runtime logs:

## 5. Fresh Initialization
- Required: YES | NO
- Environment used:
- Schema/migrations:
- Seed:
- Referential integrity:
- Critical path:

## 6. Findings / Blockers
- ...

## 7. Evidence
- [Evidence IDs]

## 8. Final Disposition
READY_TO_REVIEW | BLOCKED
```

Stop after the report. The Finalization Reviewer is the independent final check.
