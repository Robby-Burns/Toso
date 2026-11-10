# Atlas Guides — Coding Standards

## Authority
Project-specific coding standards. Builder follows these rules. Checker and Phase/Finalization Gates verify applicable rules.

## Tier 1 — Safety / Correctness
- Do not introduce known security vulnerabilities.
- Do not silently swallow errors.
- Validate external inputs.
- Preserve explicit error semantics.
- Do not weaken existing tests or verification solely to make a change pass.

## Tier 2 — Architecture
- Respect declared module and adapter boundaries.
- Avoid unnecessary coupling.
- Keep interfaces explicit.
- Do not introduce dependencies without justification.

## Tier 3 — Maintainability
- Prefer clear, local implementations.
- Avoid speculative abstractions.
- Keep tests close to the behavior they verify.
- Remove temporary/debug artifacts before phase completion.

## Tier 4 — Project Conventions
Approved project-specific conventions belong here.

A Builder may propose a Tier 4 convention. It becomes authoritative only after the project's approved standards-change process accepts it.

## Standards Change Proposal

```text
Proposal:
Reason:
Affected Code:
Compatibility Impact:
Approved By:
Approved At:
```
