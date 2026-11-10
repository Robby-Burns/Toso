# Atlas Guides V2.2

Complete replacement set tightening Atlas V2.1 without changing its core architecture.

## Canonical flow

Builder 1 → Checker 1 → Builder 2 → Checker 2 → Evaluator if unresolved → Phase Gate → next phase → Finalization Baseline → Finalizer → Finalizer Readiness Gate → Finalization Reviewer → Full Post-Finalization Verification → Finalization Gate → Complete

There is no Builder/Checker Loop 3.

## V2.2 tightening

- Checker Completion Gate with explicit AC/lens/evidence/self-audit/artifact requirements.
- Builder Completion Gate with required implementation, tests, evidence, standards, and handoff checks.
- Evaluator is evidence-only and cannot implement, edit the spec, or create Loop 3.
- Phase Gate explicitly checks functional, engineering, standards, documentation, scope/blast-radius, and repository hygiene.
- Finalization requires a recoverable baseline, bounded cleanup, full post-finalization verification, independent Finalization Review, and rollback/block behavior.
- Evidence is separate from `current-loop.md` and must resolve to runtime receipts.
- Model/provider assignments remain configurable and role documents remain model-agnostic.
