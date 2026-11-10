# Atlas Guide — Non-Coding Agent Harness

## Purpose

This guide defines the core design considerations for the **harness inside a final production agent, multi-agent system, or AI product** when the product is not primarily a coding agent.

It is a guide for **designing the product's agent harness**.

It is not:

- a replacement for the Atlas build harness;
- a second Atlas state machine;
- a requirement to use a particular framework;
- a requirement to use multiple agents;
- a requirement to use memory, RAG, queues, vector databases, or other infrastructure;
- a provider-specific implementation guide.

The goal is to give the final agent/product a strong operational foundation while keeping the architecture as small as the product allows.

---

## Core Principle

A production agent should not depend on the model alone to remain correct.

The harness should provide the controls around the model that are difficult or unsafe to leave to model behavior.

The basic relationship is:

```text
User / System Request
        ↓
      Harness
        ↓
     Agent(s)
        ↓
 Tools / Systems / Data
        ↓
      Harness
        ↓
 Result + Evidence
```

The harness owns the important boundaries.

The model provides reasoning and proposed actions.

---

# 1. Core Harness Capabilities

These are the capabilities a production agent harness should consider foundational.

Not every product will implement each one in the same way, but each should have an explicit answer.

---

## 1.1 Goal and Scope

The harness should establish:

- what the agent is trying to accomplish;
- what is in scope;
- what is out of scope;
- what constitutes completion;
- what actions are prohibited.

The agent should not be allowed to redefine its own objective simply because the task becomes difficult.

### Design question

> Can the system determine whether the agent is still working toward the authorized goal?

---

## 1.2 Context Assembly

The harness should control what information reaches the agent.

Context may come from:

- the user;
- application state;
- databases;
- APIs;
- documents;
- memory;
- previous actions;
- tool results.

The harness should distinguish trusted information from untrusted content where that distinction matters.

### Important rule

Retrieved content, tool output, documents, web pages, and stored memory should not automatically become instructions.

They are data unless the system explicitly gives them authority.

---

## 1.3 Explicit State

The harness should maintain meaningful execution state rather than relying entirely on conversation history.

At minimum, where applicable, the system should be able to determine:

```text
What was requested?
What has been attempted?
What succeeded?
What failed?
What remains?
What requires approval?
What evidence exists?
```

State should be runtime-owned when state affects authorization, workflow, or completion.

The model may reason about state.

It should not be the authoritative state store.

---

## 1.4 Planning

The harness should support planning when the task requires multiple actions.

A useful pattern is:

```text
Goal
 ↓
Tasks
 ↓
Actions
 ↓
Verification
```

Planning should be proportional to complexity.

A simple request should not require a complex planner.

A complex workflow should not rely on an implicit chain of model thoughts that the runtime cannot observe or control.

---

## 1.5 Tools

Tools should have explicit contracts.

At minimum, define where applicable:

- purpose;
- input schema;
- output schema;
- authorization;
- side-effect classification;
- timeout;
- retry behavior;
- failure behavior.

Tool availability is not the same thing as tool authorization.

The harness should decide which tools are available to a particular execution.

---

## 1.6 Authority and Permissions

The harness should establish what the agent is allowed to do.

Authority should be scoped to:

- user;
- agent;
- task;
- resource;
- tool;
- environment;
- action.

The model should not be able to grant itself additional permissions through generated text.

---

## 1.7 Execution Controls

The harness should control execution rather than allowing an agent loop to continue indefinitely.

Where applicable, establish limits for:

- execution duration;
- model calls;
- tool calls;
- retries;
- delegation;
- child executions;
- cost;
- tokens;
- external side effects.

Use only the limits that materially reduce risk.

The objective is bounded execution, not a giant resource-management subsystem.

---

## 1.8 Verification

The agent saying:

> "Done."

is not verification.

The harness should define how important outcomes are checked.

Verification may use:

- deterministic checks;
- database state;
- API responses;
- file state;
- tool receipts;
- business rules;
- independent evaluation;
- human review.

The appropriate verification method depends on the product.

### Core rule

> Do not use the thing being verified as the sole source of proof that it succeeded.

---

## 1.9 Evidence

Important execution claims should be traceable to actual runtime evidence.

Where applicable:

```text
Claim
 ↓
Evidence ID
 ↓
Runtime result
 ↓
Expected vs. actual
 ↓
Pass / Fail
```

Model-generated narrative is not authoritative execution evidence.

The harness should be able to distinguish:

```text
The agent said it happened.
```

from:

```text
The runtime can prove it happened.
```

---

## 1.10 Failure Handling

Failure paths should be designed before declaring the system production-ready.

Consider:

- tool failure;
- model failure;
- malformed output;
- timeout;
- cancellation;
- dependency outage;
- authorization failure;
- partial completion;
- ambiguous external result;
- persistence failure;
- budget exhaustion.

The system should have an explicit result for meaningful failures.

Do not convert failure into success simply to keep the workflow moving.

---

## 1.11 Blast-Radius Control

A failure should be contained as close to its source as practical.

Consider:

```text
Bad model output
    ↓
Bad tool request
    ↓
Unauthorized action
    ↓
External side effect
    ↓
Multiple downstream failures
```

The harness should place controls between these stages.

Examples:

- schema validation;
- authorization;
- approval;
- transaction boundaries;
- idempotency;
- rate limits;
- execution budgets;
- scoped credentials;
- independent verification.

### Core principle

> Prevent a local failure from becoming a system-wide failure.

---

## 1.12 Security

At minimum, consider:

- credential isolation;
- least privilege;
- input validation;
- untrusted-content boundaries;
- tenant/data isolation;
- secret redaction;
- tool authorization;
- approval enforcement;
- dependency security;
- auditability.

Security controls should be enforced by the runtime where practical.

Prompt instructions are not a substitute for runtime controls.

---

## 1.13 Observability

A production agent should provide enough telemetry to understand:

- what it was doing;
- which tools it called;
- where it failed;
- how long operations took;
- which dependencies failed;
- how much work it consumed.

Logs, traces, and metrics are operational tools.

They are not automatically acceptance evidence.

---

## 1.14 Auditability

Material actions and decisions should be attributable where the product requires it.

Consider recording:

- actor;
- execution ID;
- action;
- authorization context;
- approval;
- result;
- timestamp;
- relevant evidence.

Audit should not become a copy of every log and model response.

---

# 2. Conditional Capabilities

These are useful capabilities, but they should exist only when the product actually needs them.

---

## 2.1 Persistent Memory

Use persistent memory when the product benefits from information surviving beyond the current execution.

Before adding it, determine:

- what needs to persist;
- why state is insufficient;
- who can access it;
- who can modify it;
- how it is trusted;
- how it expires;
- how conflicts are handled.

Do not add long-term memory merely because an agent can have memory.

---

## 2.2 RAG / Retrieval

Use retrieval when the product needs external knowledge that should not live entirely in the model context.

Consider:

- source authority;
- freshness;
- retrieval quality;
- access control;
- citation/evidence;
- prompt-injection risk;
- failure behavior.

Retrieval should not automatically grant authority to the retrieved content.

---

## 2.3 Human Approval

Use approval boundaries when actions have consequences that should not be autonomous.

Typical examples include:

- irreversible actions;
- high-impact decisions;
- sensitive external communication;
- financial actions;
- production changes;
- security-sensitive actions.

Approval should be enforced by the harness.

---

## 2.4 Multi-Agent Delegation

Use multiple agents only when separating responsibilities provides a meaningful benefit.

Possible reasons include:

- independent verification;
- specialized expertise;
- isolation;
- parallel work;
- different authority boundaries.

Do not use multiple agents simply because the task is described as "agentic."

If delegation exists, bound:

- depth;
- child count;
- permissions;
- budget;
- cancellation;
- failure propagation.

---

## 2.5 Long-Running Execution

A durable workflow may be necessary when work spans:

- minutes;
- hours;
- days;
- external events;
- human approvals.

If so, the harness should make execution state durable and recoverable.

Do not build durable orchestration for a task that can safely complete in one bounded execution.

---

## 2.6 Provider Fallback

Multiple LLM or service providers can improve resilience, but fallback is not automatically safe.

Before adding fallback, consider:

- capability compatibility;
- privacy/data boundaries;
- cost;
- context compatibility;
- tool support;
- output differences;
- retry/idempotency behavior.

A fallback provider must satisfy the capability requirements of the operation.

---

## 2.7 Queues and Workers

Queues/workers may be useful for:

- long-running work;
- concurrency;
- isolation;
- scheduled processing;
- external event handling.

Do not add them merely because the system is described as "production."

---

## 2.8 Specialized Storage

Use specialized databases, vector stores, caches, or object storage when the product actually benefits from them.

Start with the simplest storage model that satisfies:

- correctness;
- scale;
- durability;
- query needs;
- security.

---

# 3. Autonomy and Approval

The harness should define the boundary between actions the agent can take automatically and actions that require additional authority.

A simple model is:

```text
READ
 ↓
NON_DESTRUCTIVE_WRITE
 ↓
REVERSIBLE_SIDE_EFFECT
 ↓
IRREVERSIBLE_SIDE_EFFECT
 ↓
HIGH_IMPACT_ACTION
```

The product decides where approval is required.

The runtime enforces the decision.

Approval for one action should not automatically authorize unrelated actions.

---

# 4. Provider and Infrastructure Agnosticism

The product should be designed so meaningful implementation choices can change without rewriting unrelated application logic.

This includes, where practical:

- LLM provider;
- model;
- database;
- vector store;
- object storage;
- queue;
- observability backend;
- deployment platform.

The target is:

> replaceable implementations, not invisible implementations.

Do not hide useful native capabilities merely for theoretical portability.

For example, if PostgreSQL provides a capability the application genuinely needs, use it.

Just make the dependency explicit.

---

# 5. Deployment Readiness

A production harness should support a predictable deployment contract.

At minimum, the system should have clear answers for:

- how it starts;
- what configuration it requires;
- how health is determined;
- how dependencies are connected;
- how secrets are provided;
- how failures surface;
- how state persists;
- how the critical workflow is verified after deployment.

The deployment platform is an implementation choice.

The application should not become architecturally dependent on the platform unless the product requirement calls for it.

---

# 6. No Happy-Path Design

The harness should be designed from failure paths as well as successful execution.

Ask:

```text
What if the model is wrong?
What if the model hallucinates a tool argument?
What if the tool is unavailable?
What if the database is unavailable?
What if the response times out?
What if the action partially succeeds?
What if the user cancels?
What if the agent retries?
What if the child agent fails?
What if retrieved content is malicious?
What if approval is missing?
What if configuration is wrong?
What if the provider changes?
What if the system restarts?
What if the evidence is missing?
```

If the answer is:

> "The agent should handle it."

that is not enough for a material control.

Determine which behavior belongs in the model and which behavior belongs in the harness.

---

# 7. Minimal Architecture

Atlas should actively resist unnecessary architecture.

Before adding a component, ask:

1. What concrete problem does it solve?
2. What failure does it prevent?
3. What boundary does it enforce?
4. Can the existing harness handle it?
5. Can configuration handle it?
6. Does it increase blast radius?
7. Does it introduce another failure mode?
8. Can it be removed independently?
9. Is there a simpler solution?

Prefer:

```text
simple + bounded + observable
```

over:

```text
complex + distributed + theoretically scalable
```

unless the actual requirements justify the latter.

---

# 8. Final Harness Review

Before declaring a final agent/product production-ready, review the harness against these questions.

## Goal

- Is the objective explicit?
- Is scope bounded?
- Is completion defined?

## Context

- Is context deliberately assembled?
- Are trusted and untrusted sources distinguished?
- Can external content inject instructions?

## State

- Is important state runtime-owned?
- Can the system distinguish attempted, completed, failed, and remaining work?

## Tools

- Are tools typed?
- Are permissions enforced?
- Are side effects classified?
- Are failures explicit?

## Execution

- Is execution bounded?
- Are retries bounded?
- Is cancellation handled?
- Can failures cascade?

## Authority

- What can the agent do autonomously?
- What requires approval?
- Can delegation expand authority?

## Verification

- How does the system know it succeeded?
- Can the agent itself fabricate the proof?

## Evidence

- Can important claims be traced to runtime evidence?
- Are evidence and observability kept distinct?

## Security

- Are secrets isolated?
- Is least privilege enforced?
- Are untrusted inputs contained?
- Are cross-user/tenant boundaries enforced?

## Production

- Can it deploy predictably?
- Can it recover?
- Can operators understand failures?
- Can a provider or infrastructure component be replaced where practical?

## Complexity

- Is every major component justified?
- What can be removed without reducing required capability?

---

# 9. The Atlas Rule

The final agent harness should be:

```text
Purposeful
Bounded
Secure
Observable
Verifiable
Replaceable where practical
Production-ready
```

But above all:

> **Build the smallest harness that gives the agent the controls it actually needs.**

More agents, more tools, more memory, more infrastructure, and more orchestration do not automatically make an agent better.

Every additional component creates another place to fail.

The objective is not maximum architecture.

The objective is **reliable capability with controlled blast radius**.
