# Atlas Guides — Project Specification Template

## Status
Template for an approved project specification.

## Purpose
Defines the project contract that Builder and Checker use for story-level implementation and verification.

## 1. Approval State

```text
Status: DRAFT | APPROVED | AMENDED | BLOCKED
Approved By: [human owner]
Approved At: [timestamp]
Version: [version]
```

Only `APPROVED` or an explicitly approved `AMENDED` version is authoritative for execution.

## 2. Project Objective

[Approved objective.]

## 3. Scope

### In Scope
- [item]

### Out of Scope
- [item]

## 4. Phases

For each phase:

```text
Phase: [ID]
Name: [name]
Risk: LOW | MEDIUM | HIGH
Exit Condition:
  - [condition]
```

## 5. Stories

Each story must define:

```text
Story ID: [X.Y]
Title: [title]
Risk: LOW | MEDIUM | HIGH
Dependencies: [IDs]
Scope: [files/components/interfaces]
Acceptance Criteria:
  AC-01: [criterion]
  AC-02: [criterion]
Rejection Surface:
  - [invalid input / failure behavior]
```

## 6. Specification Defect

If implementation reveals that the specification is contradictory, incomplete, or materially wrong:

1. Evaluator records `SPEC_DEFECT`.
2. Story becomes `BLOCKED`.
3. No agent silently changes this file.
4. Human owner decides whether to amend the specification.
5. Approved amendment receives a new version.
6. `build-plan.md` is reconciled.
7. Affected stories are re-evaluated for eligibility.

## 7. Change Control

Every approved amendment records:

```text
Version:
Date:
Change:
Reason:
Affected Phases:
Affected Stories:
Approved By:
```
