# Atlas Guides — Kernel

## Purpose
Governing contract for Atlas roles, directives, harnesses, and project artifacts.

## Authority
1. Human-approved decisions are authoritative.
2. `spec.md` is the approved project contract.
3. `build-plan.md` sequences approved work.
4. `.build-context.md` stores durable project context.
5. `current-loop.md` stores compact active-story state and evidence references.
6. Runtime execution receipts are authoritative for machine-verifiable execution facts.
7. Model output is never proof by itself.

## Role Separation
- Builder constructs.
- Checker independently verifies.
- Evaluator adjudicates unresolved story disputes.
- Phase Gate verifies phase completion.
- Finalizer performs bounded whole-build cleanup and final operational verification.
- Finalization Reviewer independently verifies the Finalizer's work and final runtime readiness.

## Two-Pass Rule
Every story has at most two complete Builder → Checker passes:
Builder 1 → Checker 1 → Builder 2 → Checker 2.
There is no Loop 3. HIGH/CRITICAL risk increases verification depth, not loop count.

## Evaluator
Evaluator may adjudicate evidence and issue a defined disposition. It may not implement code, modify source, silently edit `spec.md`, or create Loop 3.

## Checker PASS
A story is verified only when every required AC is VERIFIED, every mandatory lens is complete, required evidence is valid, no required behavior is UNVERIFIED, no blocking finding remains, required regression checks pass, and the Checker Completion Gate is satisfied.

Zero reproduced bugs alone is never sufficient for PASS.

## Evidence
Execution claims must resolve to actual runtime receipts. Model narrative is not a receipt.

## Scope, Standards, and Blast Radius
Unexpected changes, unexplained dependencies, suspicious artifacts, and uncertain cleanup candidates must be surfaced. "Unused" does not automatically mean "safe to delete."

Any issue must be evaluated for blast radius, not only local correctness. Consider at minimum:
- startup/deployability;
- data integrity and persistence;
- security/authentication/authorization;
- public APIs and contracts;
- shared utilities and cross-feature behavior;
- external integrations;
- user-visible workflows;
- configuration and infrastructure;
- test/verification coverage.

An issue affecting startup, data integrity, security, or the primary user workflow is potentially project-blocking even if the local code change is small.

## Model Independence
Role/directive documents are provider/model agnostic. Runtime assignments belong in configuration.

## Finalization
Finalization occurs once after all phases pass. A recoverable baseline is created before Finalizer changes. Full configured verification runs afterward.

Finalization is not complete merely because repository tests pass. If a deployment target exists, finalization also requires evidence that the intended runtime artifact can start and that the critical runtime path works in that environment.

For database-backed projects where startup, migration, schema initialization, or seeding is part of the application contract, fresh-environment initialization must be verified using an isolated/disposable environment or equivalent safe mechanism. Never destroy or reset production data merely to satisfy this requirement.

## Finalization Reviewer
The Finalization Reviewer is independent of the Finalizer. It reviews the Finalizer diff, cleanup decisions, blast radius, engineering checks, deployment/runtime behavior, and final product invariants. It does not modify code.

A final PASS requires sufficient evidence for all applicable finalization and runtime categories. Missing deployment/runtime evidence cannot be silently converted to PASS.

## Runtime Enforcement
Markdown defines the protocol. Executable tooling should enforce legal transitions, loop count, evidence validation, configured command execution, gates, finalization/review sequencing, remediation limits, and rollback where available.
