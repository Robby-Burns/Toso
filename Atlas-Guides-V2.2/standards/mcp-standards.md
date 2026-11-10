# Atlas Standard Integration — MCP Builder

## Purpose
Domain-specific MCP engineering requirements loaded conditionally by the general Atlas harness.

## Integration

MCP stories use the normal Atlas lifecycle:

```text
Builder → Checker → Builder → Checker → Evaluator if needed → Phase Gate
```

MCP does not create a separate workflow.

## Conditional Loading

```text
Story Type = MCP
→ load this standard
→ add MCP-specific Checker requirements
```

## General Capabilities Reinforced

- risk-based verification;
- evidence-backed execution;
- contract/failure/boundary testing;
- controlled learning.

## MCP-Specific Requirements

When applicable, verify:

- canonical tool contracts;
- scoped permissions;
- sandboxing;
- credential isolation;
- no secret leakage;
- normalized errors;
- safe retries;
- idempotency;
- timeout behavior;
- mocked external failure paths;
- appropriate approval gates for state-changing operations.

## Learning

MCP observations may produce candidate lessons, but learning never silently changes protected runtime behavior.
