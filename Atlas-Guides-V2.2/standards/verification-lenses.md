# Mandatory Verification Lenses

## Purpose

This document defines the canonical verification-lens registry for the Atlas lifecycle.

The registry is the single source of truth for the dimensions the Checker must evaluate when independently verifying a story.

Every story checked by the Checker MUST use every mandatory lens unless the verification packet and approved story specification establish that a lens is genuinely not applicable. Any N/A determination MUST include a concrete rationale.

Builder tests and Builder-provided evidence are inputs to verification, but they are not the Checker test plan.

The Checker is responsible for independently determining whether the current implementation satisfies the approved story contract.

---

# Canonical Mandatory Lenses

The following nine lenses are mandatory for every story:

1. Boundary
2. Security
3. Failure Paths
4. Skeptic
5. Red Team
6. QA Edge
7. Regression
8. Runtime
9. Blast Radius

These nine lenses are the complete canonical registry.

A story MUST NOT omit a mandatory lens merely because:

- the Builder did not test it;
- the Builder considers it low risk;
- the Builder's tests already passed;
- the lens appears similar to another lens;
- the implementation is small;
- the Checker believes the lens is unlikely to find a problem.

If a lens is genuinely not applicable, the Checker MUST record the applicability decision and rationale in the required lens record.

---

# Lens 1 — Boundary

## Purpose

Verify that the implementation correctly enforces the boundaries defined by the story contract.

## Checker Questions

The Checker should determine:

- What inputs are allowed?
- What inputs are prohibited?
- What outputs are allowed?
- What outputs are prohibited?
- Where are the exact acceptance and rejection boundaries?
- What happens exactly at a threshold?
- What happens immediately below and above a threshold?
- Can the implementation be driven outside its approved scope?
- Are invalid or out-of-contract values rejected appropriately?
- Are scope boundaries enforced consistently across relevant entry points?

## Required Verification

Where applicable, independently test:

- minimum values;
- maximum values;
- exact thresholds;
- immediately-inside values;
- immediately-outside values;
- empty inputs;
- null values;
- malformed inputs;
- unexpected combinations;
- prohibited states;
- alternate entry points that may bypass boundary enforcement.

The Checker MUST verify the actual boundary behavior rather than relying solely on implementation intent or Builder assertions.

---

# Lens 2 — Security

## Purpose

Verify security-relevant controls, trust boundaries, and unsafe access paths applicable to the story.

Security is broader than secret handling.

## Checker Questions

Where applicable, the Checker should determine:

- Are authentication requirements enforced?
- Are authorization requirements enforced?
- Can an unauthorized caller access the behavior?
- Can a caller obtain data they should not receive?
- Are sensitive data flows appropriately constrained?
- Are secrets protected?
- Are trust boundaries respected?
- Are unsafe access paths available?
- Can direct or lower-level invocation bypass security controls?
- Can validation or authorization be circumvented through alternate interfaces?
- Are security-relevant failures handled safely?

## Required Verification

The Checker should consider applicable attack paths such as:

- authorized versus unauthorized callers;
- normal versus direct invocation;
- public versus internal interfaces;
- validated versus unvalidated entry points;
- expected versus manipulated identifiers;
- normal versus malformed requests;
- access through alternate interfaces;
- attempts to bypass authorization or validation;
- inappropriate data exposure;
- trust-boundary crossings.

A Security lens MUST NOT be marked N/A merely because the story does not handle secrets.

If authentication, authorization, data exposure, trust boundaries, or unsafe access paths are relevant, they MUST be considered.

If the lens is genuinely N/A, the Checker MUST explicitly explain why the story contains no applicable security, authorization, data exposure, secret-handling, or trust-boundary concern.

---

# Lens 3 — Failure Paths

## Purpose

Verify behavior when expected operations, dependencies, inputs, tools, services, or downstream systems fail or become unusable.

## Checker Questions

The Checker should determine:

- What happens when a required dependency fails?
- What happens when an external service is unavailable?
- What happens when a tool returns an error?
- What happens on timeout?
- What happens when an operation partially completes?
- What happens when an input is unusable?
- Does the system fail safely?
- Are errors surfaced with the required semantics?
- Can partial failure leave invalid or inconsistent state?
- Are retries safe?
- Is duplicate execution handled correctly?
- Does recovery preserve the story contract?

## Required Verification

Where applicable, independently exercise:

- dependency failure;
- timeout;
- malformed dependency response;
- unavailable service;
- rejected operation;
- partial completion;
- retry;
- duplicate execution;
- interrupted execution;
- stale state;
- inconsistent state;
- recovery after failure.

Failure behavior MUST be evaluated against the approved contract, not merely against whether an exception was raised.

---

# Lens 4 — Skeptic

## Purpose

Challenge the Builder's evidence, reasoning, assumptions, and conclusions.

The Skeptic lens asks whether the evidence actually establishes the claim being made.

## Checker Questions

The Checker should determine:

- Does the evidence actually prove the claimed behavior?
- Is the test testing the requirement or merely a nearby behavior?
- Does the evidence establish the full acceptance criterion?
- Are conclusions broader than the evidence supports?
- Are important assumptions untested?
- Are there gaps between implementation evidence and contract requirements?
- Is the Builder relying on a happy path to support a broader claim?
- Does a passing test actually demonstrate the property being claimed?
- Is absence of a reproduced bug being treated as proof that no bug exists?

## Required Verification

The Checker MUST distinguish between:

1. what the evidence demonstrates; and
2. what the Builder claims the evidence demonstrates.

The Checker MUST NOT accept a broader conclusion than the evidence supports.

For example, a test demonstrating deterministic output for identical inputs establishes deterministic behavior for the tested condition. It does not, by itself, establish that the implementation has no hidden state under every possible execution condition.

The Checker should challenge:

- unsupported assertions;
- incomplete evidence;
- over-broad conclusions;
- untested assumptions;
- evidence that does not establish the stated requirement;
- tests that pass while failing to exercise the actual contract.

Builder evidence may be valid evidence, but Builder evidence is never automatically sufficient merely because it exists or because the test suite passes.

---

# Lens 5 — Red Team

## Purpose

Actively attempt to break the story contract.

The Red Team lens is the primary adversarial verification mechanism.

The Checker MUST independently attempt to disprove the implementation rather than merely confirm Builder claims or reproduce Builder tests.

A negative test is not automatically a Red Team test.

A test satisfies the Red Team purpose when it represents an independent attempt to circumvent, bypass, misuse, break, or otherwise defeat the implementation's contract.

## Independent Adversarial Requirement

For every applicable story, the Checker MUST:

1. identify plausible attack or failure hypotheses;
2. identify hypotheses that are not merely repetitions of Builder tests;
3. construct adversarial scenarios around those hypotheses;
4. attempt to execute those scenarios using the available runtime and tooling;
5. test plausible alternate paths or bypasses;
6. record the actual adversarial attempt and its result.

Builder tests are evidence available to the Checker.

They are not the complete Red Team test plan.

The Checker MUST ask:

> **What could I try that the Builder may not have thought to try?**

and:

> **Is there another path by which this requirement could be bypassed?**

## Required Adversarial Considerations

Where applicable, the Checker MUST consider:

- independent attack hypotheses;
- no-easy-path / bypass testing;
- alternate execution paths;
- alternate interfaces;
- direct lower-level invocation;
- malformed inputs;
- unexpected inputs;
- boundary manipulation;
- repeated actions;
- reordered actions;
- conflicting actions;
- invalid state transitions;
- stale state;
- partial completion;
- duplicate execution;
- retry behavior;
- attempts to circumvent validation;
- attempts to circumvent authorization;
- attempts to reach prohibited states;
- inconsistent or conflicting inputs;
- plausible legitimate misuse;
- plausible unauthorized abuse;
- dependency manipulation;
- unexpected tool behavior;
- attempts to exploit assumptions made by the Builder;
- evidence of the actual adversarial attempt and its result.

The exact scenarios depend on the story.

The Checker does not need to manufacture unrealistic attacks merely to satisfy a checklist. The objective is to exercise credible ways the implementation could violate the approved contract.

## No-Easy-Path Test

For each applicable requirement, the Checker should explicitly consider whether there is another route around the control.

Potential alternate paths include:

- UI versus API;
- public versus internal interface;
- normal versus direct invocation;
- validated versus unvalidated entry point;
- expected versus reordered operations;
- first versus repeated execution;
- authorized versus unauthorized caller;
- complete versus partial execution;
- current versus stale state;
- normal versus malformed payload;
- high-level function versus lower-level helper;
- expected workflow versus manually manipulated state.

The absence of an obvious bypass is not proof that no bypass exists.

The Checker should test plausible bypasses rather than merely stating that no bypass was observed.

## Bug-Seeking Requirement

Before reaching a final verdict, the Checker MUST identify at least one plausible failure mode for every applicable story.

The Checker should deliberately search for credible failures.

If a credible scenario cannot be executed because of a runtime, dependency, permission, tooling, or environmental limitation, the limitation MUST be recorded.

An inability to test a failure mode MUST NOT be represented as evidence that the failure mode does not exist.

## Red Team Evidence

Each substantive adversarial attempt SHOULD be recorded using:

- **Attack Hypothesis**
- **Attempt**
- **Expected**
- **Actual**
- **Evidence**
- **Result**

A Red Team lens MUST NOT be marked VERIFIED solely because:

- Builder tests passed;
- the Checker ran the same tests;
- a happy path worked;
- a negative test passed;
- the Checker inspected the code;
- no obvious defect was noticed.

## Survival Standard

A VERIFIED Red Team result means:

> The implementation survived the specific adversarial scenarios that were actually attempted.

It does NOT mean:

- the implementation is bug-free;
- the implementation is security-proof;
- all possible bypasses have been discovered;
- no undiscovered defect exists.

The Checker verifies the strength of the evidence and the implementation's survival against credible tested attacks.

---

# Lens 6 — QA Edge

## Purpose

Verify less-common, unusual, and edge-case behavior that is relevant to the story contract.

## Checker Questions

The Checker should consider:

- empty values;
- null values;
- zero;
- negative values;
- maximum values;
- minimum values;
- unusual combinations;
- malformed structures;
- unexpected ordering;
- repeated operations;
- boundary-adjacent values;
- unusual but legitimate inputs;
- combinations that may expose assumptions.

QA Edge focuses on correctness under unusual conditions.

It should not be treated as a substitute for the broader adversarial requirements of the Red Team lens.

---

# Lens 7 — Regression

## Purpose

Verify that the story implementation does not break existing behavior that should remain intact.

## Checker Questions

The Checker should determine:

- Do existing tests still pass?
- Does existing behavior remain intact?
- Did the change alter unrelated functionality?
- Did shared interfaces change unexpectedly?
- Did an existing contract regress?
- Did the implementation introduce failures outside the story's intended behavior?

## Required Verification

Where applicable, perform:

- targeted regression tests;
- relevant existing test suites;
- integration tests;
- interface compatibility checks;
- regression checks around changed behavior.

A full test suite is valuable evidence but does not automatically establish absence of every regression.

The Checker should use the story scope and changed surfaces to determine which regression risks deserve additional attention.

---

# Lens 8 — Runtime

## Purpose

Verify the implementation in the actual configured runtime using the project's authoritative tooling and evidence mechanisms.

## Checker Questions

The Checker should determine:

- Does the implementation actually execute?
- Does it work through the configured runtime?
- Are public interfaces functioning as expected?
- Are dependencies available and correctly integrated?
- Does the observed runtime behavior match the contract?
- Are runtime failures being hidden by mocks, static inspection, or isolated unit tests?
- Are execution results supported by authoritative evidence?

## Required Verification

The Checker MUST use the applicable Project Tooling Profile and authoritative runtime receipts when required.

Where applicable, verify through:

- public APIs;
- configured application interfaces;
- actual service execution;
- integration boundaries;
- configured databases;
- configured tools;
- configured containers;
- configured runtime environments.

Static inspection alone is insufficient when actual execution is required by the story or verification packet.

Every execution claim MUST have valid Evidence IDs when the evidence contract requires them.

---

# Lens 9 — Blast Radius

## Purpose

Verify that the implementation's effects remain within approved scope and do not create unintended consequences in adjacent components, interfaces, workflows, or shared behavior.

## Checker Questions

The Checker should determine:

- What files and components changed?
- What interfaces are affected?
- What shared code is affected?
- What adjacent workflows depend on the changed behavior?
- Could the change alter unrelated behavior?
- Could shared state or configuration be affected?
- Could global state or environment changes affect other components?
- Are side effects within the approved story scope?
- Could the implementation create unintended downstream effects?

## Required Verification

Where applicable, examine:

- changed files;
- changed interfaces;
- shared utilities;
- shared configuration;
- global state;
- environment dependencies;
- database effects;
- downstream consumers;
- adjacent workflows;
- integration boundaries.

Deterministic output alone does not prove that there is no hidden I/O, global side effect, or unintended effect on adjacent components.

Full regression testing is valuable evidence, but Blast Radius verification should also consider the actual surfaces changed and the potential consequences of those changes.

---

# Lens Applicability

All nine lenses are mandatory by default.

A lens MAY be marked N/A only when the verification packet and/or approved story specification establishes a concrete reason that the lens does not apply.

The Checker MUST record:

- **Applicability:** Applicable or N/A
- **Applicability Rationale:** Required when N/A

Uncertainty is not sufficient justification for N/A.

If the Checker cannot determine whether a lens applies, the appropriate result is **UNVERIFIED**, not N/A.

Examples of insufficient N/A rationales:

- "Not tested."
- "Builder did not mention it."
- "No obvious risk."
- "No secrets involved." — for the entire Security lens.
- "Small change."
- "Unit tests passed."

---

# Required Lens Record

Every mandatory lens MUST have a complete verification record.

Each record MUST contain:

- **Lens**
- **Applicability**
- **Applicability Rationale** — required when N/A
- **Question Tested**
- **Action / Command / Inspection**
- **Evidence IDs**
- **Expected**
- **Actual**
- **Result**

The Result MUST be one of:

- **VERIFIED**
- **FAILED**
- **UNVERIFIED**
- **N/A** where the lens has been established as not applicable

A lens MUST NOT be marked VERIFIED solely because the Checker states that it was reviewed.

The record must contain substantive evidence supporting the result.

---

# Evidence Discipline

The Checker MUST maintain evidence discipline throughout verification.

## Execution Claims

Any claim that an action was executed MUST be supported by the appropriate Evidence ID.

Examples include:

- tests executed;
- commands executed;
- API calls executed;
- integration checks executed;
- runtime behavior observed;
- adversarial scenarios attempted;
- regression suites executed.

## Evidence IDs

Every Evidence ID MUST:

1. resolve to an actual runtime or inspection receipt;
2. correspond to the claimed action;
3. support the claimed result.

The Checker MUST NOT:

- fabricate Evidence IDs;
- invent execution results;
- claim commands were run when they were not;
- treat intended commands as executed commands;
- treat unavailable evidence as successful evidence.

If required evidence cannot be produced, the applicable result MUST reflect that limitation.

---

# Relationship Between Lenses and Test Techniques

A lens defines **what dimension of correctness is being evaluated**.

A test technique defines **how the Checker attempts to evaluate it**.

These are intentionally different concepts.

For example:

- **Boundary** asks whether scope and threshold boundaries are enforced.
- **QA Edge** asks whether unusual edge conditions behave correctly.
- **Failure Paths** asks what happens when operations fail.
- **Security** asks whether security and trust boundaries hold.
- **Skeptic** asks whether the evidence actually proves the claim.
- **Red Team** asks how the implementation could be broken or bypassed.
- **Regression** asks whether existing behavior remains intact.
- **Runtime** asks whether the implementation actually works in the configured environment.
- **Blast Radius** asks whether effects remain within approved scope.

A single test may provide evidence for multiple lenses, but the Checker MUST evaluate whether it actually satisfies the distinct question of each lens.

The Checker MUST NOT collapse multiple lenses into one generic "tests passed" conclusion.

---

# Builder Evidence Is Not Checker Verification

Builder-provided tests, claims, screenshots, logs, and evidence may be reused as evidence when they are valid and relevant.

However:

> **Builder evidence is evidence, not the Checker test plan.**

The Checker MUST independently determine whether the evidence establishes the story contract.

This is especially important for:

- Red Team;
- Skeptic;
- Security;
- Boundary;
- Failure Paths.

The Checker should not simply rerun the Builder's test suite and declare independent verification complete.

---

# Mandatory Adversarial Posture

The Checker should approach verification with the following mindset:

> **The Builder has demonstrated that the implementation works under the scenarios they tested. Now determine what they may not have tested.**

The Checker should actively search for:

- assumptions;
- bypasses;
- alternate paths;
- invalid states;
- unexpected combinations;
- boundary failures;
- failure recovery problems;
- authorization gaps;
- stale-state problems;
- duplicate execution;
- partial completion;
- evidence gaps;
- scope violations;
- unintended side effects.

The goal is not to manufacture failures.

The goal is to discover credible failures that ordinary happy-path verification could miss.

---

# Completion Requirements

Before the Checker can return a final VERIFIED verdict:

- every acceptance criterion has been evaluated;
- every mandatory lens has a complete record;
- every applicable lens has substantive evidence;
- every N/A lens has a concrete rationale;
- every required execution claim has valid Evidence IDs;
- Evidence IDs resolve to actual receipts;
- evidence supports the claimed action and result;
- the Skeptic lens has challenged the sufficiency of Builder evidence;
- the Red Team lens has performed genuine independent adversarial verification where applicable;
- at least one plausible failure mode has been considered for the story;
- required regression checks have been completed;
- the Checker coverage self-audit has been completed;
- findings have been recorded where applicable;
- the final verdict is supported by the evidence.

Failure to satisfy a required verification condition MUST NOT be silently converted into VERIFIED.

If required verification cannot be completed, the appropriate result is UNVERIFIED.

---

# Final Principle

The verification lenses exist to prevent verification from becoming a rubber stamp.

The Checker is not responsible for proving that the Builder followed the expected process.

The Checker is responsible for determining whether the implementation actually survives independent verification against the approved story contract.

The central question is:

> **"What evidence would convince me that this implementation is wrong—and did I actually try to obtain that evidence?"**

A strong Checker does not merely confirm that the Builder's tests pass.

A strong Checker attempts to find the thing those tests did not think to try.