# Atlas Guides — Evidence Contract

## Purpose

Define machine-verifiable proof used by Atlas workflow decisions.

## Runtime Execution Receipt

A runtime-generated receipt should contain:

```text
Evidence ID:
Timestamp:
Actor:
Operation:
Command / Action:
Exit Code / Result:
Output Reference:
Working Directory:
Related Story:
Related Acceptance Criterion:
```

## Evidence States

```text
VALID
INVALID
MISSING
CONFLICTING
```

## Validity

Evidence is valid only when the runtime can resolve the Evidence ID to an actual receipt.

A model-authored statement is not a receipt.

Examples of invalid proof:

```text
"I ran pytest and everything passed."
```

without a corresponding runtime receipt.

```text
Evidence ID: EVID-999
```

when no such receipt exists.

## Claim-to-Evidence Rule

For every execution-based Checker claim:

```text
Claim
  ↓
Evidence ID
  ↓
Runtime Receipt
  ↓
Actual execution result
```

If that chain breaks, the claim is unsupported.

## Lens-to-Evidence Rule

Every mandatory lens must have evidence appropriate to the behavior being evaluated.

A single full-suite test receipt may support regression, but it does not automatically prove that Boundary, Failure Paths, Security, or other lenses were independently evaluated.

The Checker must connect evidence to the specific lens it supports.

## Current Loop

`current-loop.md` stores Evidence IDs and conclusions.

It is an index/audit summary, not the authoritative execution store.

## State

Evidence validity is evaluated by the harness.

The Checker does not create legal state transitions by citing evidence.
