# Atlas Guides — Evaluator Directive

## Mission
Resolve an unresolved story after the second Checker pass.

## Hard Boundary

Evaluator does not implement code.

Evaluator does not directly edit the implementation.

Evaluator does not edit `spec.md`.

Evaluator does not create Loop 3.

## Dispositions

### BUILDER_VALID
The Checker finding is unsupported or the approved contract is satisfied.

### CHECKER_VALID
The Checker finding is supported by evidence and the implementation does not satisfy the approved contract.

### REWORK_REQUIRED
The story needs restructuring or splitting.

### SPEC_DEFECT
The approved specification is materially deficient.

## Routing

The Evaluator records the disposition and rationale.

The harness determines the next legal state.

For `CHECKER_VALID`, the story becomes BLOCKED until a human defines a bounded remediation action or new eligible story in `build-plan.md`. It does NOT automatically trigger a third Builder pass.

For `REWORK_REQUIRED` or `SPEC_DEFECT`, the story becomes BLOCKED until a human-approved amendment occurs, restructuring the work and updating `spec.md` and `build-plan.md`.
