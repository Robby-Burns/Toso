# Atlas Guides — Reusable Failure Patterns

**Status:** Cross-Project Reusable Adversarial Knowledge

**Authority:** Patterns are reusable verification targets. They do not replace project-specific acceptance criteria.

New patterns should be added only after deterministic evidence demonstrates that the failure is real and the pattern is sufficiently general to apply across projects.

## Pattern Catalog

### PAT-001: Silent Payload Corruption on 200 OK
- **Description:** External API returns success while required fields are missing, malformed, or empty.
- **Lens:** QA Edge / Skeptic
- **Attack:** Return a superficially successful but contract-invalid payload and verify rejection before domain processing.
- **Applicable:** External APIs/RPC/SDK integrations.

### PAT-002: Non-Idempotent Retry Duplication
- **Description:** Retry repeats a mutation after an ambiguous network outcome.
- **Lens:** QA Edge / Red Team
- **Attack:** Simulate timeout after mutation and verify duplicate protection.
- **Applicable:** Writes, payments, messaging, database mutations.

### PAT-003: Asymmetric Authorization Enforcement
- **Description:** Read operations enforce authorization while write/update/delete paths omit it.
- **Lens:** Security / Red Team
- **Attack:** Exercise write/delete with insufficient privileges.
- **Applicable:** Security boundaries and admin operations.

### PAT-004: Swallowed Exception Degradation
- **Description:** Infrastructure failures are swallowed and replaced with apparently valid defaults.
- **Lens:** Skeptic
- **Attack:** Inject dependency/database failure and inspect resulting state/error behavior.
- **Applicable:** External adapters, storage, parsing, databases.

### PAT-005: Path Traversal & Unbounded File Operations
- **Description:** File paths are accepted without canonicalization/containment controls.
- **Lens:** Red Team / Security
- **Attack:** Exercise traversal and absolute paths.
- **Applicable:** File storage and artifact tools.

### PAT-006: Secret Leakage in Telemetry & State
- **Description:** Credentials appear in logs, state files, errors, or artifacts.
- **Lens:** Security
- **Attack:** Trigger credential-bearing failures and inspect logs/state.
- **Applicable:** Credential-bearing integrations.

### PAT-007: Production-Only Runtime Dependency Failure
- **Description:** Local tests pass, but the deployed environment lacks a required runtime library, native dependency, driver, binary, or compatible package.
- **Lens:** Runtime / Skeptic
- **Attack:** Build and deploy the intended artifact; inspect startup logs and execute the health path.
- **Applicable:** Docker/Railway/Cloud Run/GKE/etc., database drivers, native extensions, OS packages.

### PAT-008: Startup Caller/Callee Contract Drift
- **Description:** Application startup calls an initialization function with a signature that no longer matches the deployed implementation.
- **Lens:** Runtime / Boundary
- **Attack:** Start the deployed application and exercise the lifespan/startup path. Inspect the actual function signature and invocation.
- **Applicable:** FastAPI/ASGI lifespan, CLI startup, workers, dependency injection.

### PAT-009: Fresh-Database Seed Referential-Integrity Failure
- **Description:** Tests pass against an existing or fixture-populated database, but clean initialization fails because seed records are inserted in an order that violates foreign keys or other constraints.
- **Lens:** Boundary / Failure Paths / Runtime
- **Attack:** Initialize a disposable empty PostgreSQL-equivalent database from scratch and execute the real migration/schema/seed path.
- **Applicable:** Database-backed applications with migrations or seed data.

### PAT-010: Test-Environment / Production-Database Semantics Drift
- **Description:** SQLite, mocks, fixtures, or an already-seeded test database behave differently from the production database engine or clean initialization path.
- **Lens:** Boundary / Regression
- **Attack:** Run the critical initialization and data operations against the actual production-compatible engine in an isolated environment.
- **Applicable:** SQLite/PostgreSQL differences, ORM-backed applications, integration tests.

### PAT-011: Critical Runtime Path Not Exercised After Cleanup
- **Description:** Repository checks pass after finalization, but the primary user workflow fails in the intended runtime environment.
- **Lens:** Runtime
- **Attack:** Deploy the final artifact and exercise the smallest complete critical user journey.
- **Applicable:** Deployed applications.

### PAT-012: Cleanup Blast Radius Hidden by Local Reference Search
- **Description:** A file appears unused by static text search but is loaded dynamically, referenced by configuration/packaging, required at startup, or used by deployment scripts.
- **Lens:** Skeptic / Blast Radius
- **Attack:** Inspect packaging, dynamic imports, configuration, startup registration, scripts, and deployed behavior before deletion.
- **Applicable:** Whole-project cleanup.

## Pattern Submission Criteria

A new reusable pattern should:
1. Be supported by deterministic evidence.
2. Have a reproducible verification strategy.
3. Be general enough to apply beyond one business feature.
4. Record the blast radius and failure boundary.
5. Avoid encoding provider/model-specific behavior.

Patterns may be used to strengthen Checker and Finalization Reviewer search strategies, but they do not justify inventing defects without evidence.
