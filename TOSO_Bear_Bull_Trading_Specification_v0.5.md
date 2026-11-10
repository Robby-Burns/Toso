---
status: DRAFT
version: 0.5
project: TOSO — Bear & Bull Trading
owner:
approved_by:
approved_at:
---

# TOSO — Bear & Bull Trading
## Project Specification

> **Trade mechanically. Let winners run. Cut what is wrong. Learn without improvising.**

---

# 1. Project Overview

## 1.1 Name

**TOSO — Bear & Bull Trading**

## 1.2 Objective

Build an autonomous U.S. equities trading and research system that executes a deterministic trend-following strategy while protecting capital through deterministic risk and execution controls.

TOSO is designed around a simple asymmetry:

- lose small when a trade is wrong;
- add risk only when a trade is proving itself right;
- allow valid trends to continue without arbitrary profit-taking;
- exit when the trade's defined invalidation is reached;
- learn from results through a separate research process rather than changing behavior during trading.

## 1.3 Problem

Most automated trading systems become increasingly complex as they attempt to predict, optimize, adapt, or react to every market condition.

That complexity can undermine the behavioral edge of trend following.

TOSO instead separates three responsibilities:

1. **Trader** — executes deterministic trading rules.
2. **Guardian** — protects capital, execution integrity, and system boundaries.
3. **Scientist** — studies results and proposes improvements without controlling execution.

## 1.4 Desired Outcome

When complete, TOSO must behave like a disciplined trend follower:

> **Find a trend → enter with defined risk → add only when winning → let the winner run → exit when invalidated → learn from what happened.**

The system must not improvise these behaviors in production.

---

# 2. TOSO Philosophy

These principles define the permanent character of the system.

## 2.1 Add to Winners

TOSO may increase exposure to a position only when the existing position is demonstrating sufficient strength under deterministic strategy and risk rules.

Adding is earned by market behavior.

It is never based on hope, conviction, or a desire to recover a loss.

## 2.2 Let Winners Run

A profitable position is not an automatic reason to reduce exposure.

TOSO must not:

- use a fixed profit target as the normal exit;
- trim solely because a position has appreciated substantially;
- trim solely because the position has become a large percentage of the portfolio;
- close a valid trend merely because a predefined return has been reached.

A valid winner remains eligible to run until its strategy-defined exit or another independent safety condition requires action.

## 2.3 Never Average Down

TOSO must never increase exposure to a position because that position has declined.

A declining position cannot earn additional risk merely by becoming cheaper.

## 2.4 Never Add Risk to Losers

New exposure to an existing position requires that the position satisfy the deterministic conditions for a winner/pyramid.

Loss status cannot be overridden by conviction, expected recovery, valuation, or Agent recommendation.

## 2.5 Cut Invalidated Losses

Every Core position must have a deterministic invalidation established before execution.

When that invalidation is reached, TOSO exits the position according to its execution and broker-safety rules.

There is no discretionary grace period, thesis rewrite, or Agent override.

## 2.6 Concentration Controls New Exposure

Concentration is an **intake constraint**, not a forced-exit mechanism.

When a position reaches a concentration limit:

- additional exposure may be blocked;
- unrelated valid positions remain governed by their own rules;
- a valid winner is not sold merely to restore a target allocation;
- concentration does not modify the strategy's entry, invalidation, or normal exit logic.

## 2.7 Learn Without Improvising

TOSO learns through research.

Production behavior does not change because of a single trade, unusual market event, Agent observation, or emotional interpretation.

Research must progress through evidence and validation before a production change is considered.

## 2.8 The AI Does Not Control Money

The Agent may:

- observe;
- analyze;
- research;
- journal;
- identify patterns;
- generate hypotheses;
- design experiments;
- analyze experiment results;
- propose changes.

The Agent may not:

- determine executable order quantity;
- determine executable risk;
- determine stops or invalidations;
- place orders;
- cancel orders;
- override the Execution Gate;
- override risk controls;
- override emergency controls;
- change production configuration without human approval.

---

# 3. Glossary

| Term | Definition |
|---|---|
| Core | Primary trend-following trading system and its capital allocation. |
| Black Swan | Independent defensive/convex protection system operating separately from Core. |
| Trader | Deterministic production trading logic responsible for strategy behavior. |
| Guardian | Deterministic controls protecting risk, capital, execution, and system integrity. |
| Scientist | AI research system responsible for observation, analysis, experimentation, and proposals. |
| Winner | A position that satisfies the deterministic conditions for continued exposure and/or pyramiding. |
| Loser | A position that does not satisfy the deterministic conditions for continued exposure and/or pyramiding. |
| Invalidation | Deterministic condition established for a trade that indicates the trade thesis has failed. |
| Pyramiding | Adding exposure to an existing winning position under deterministic rules. |
| Intake Constraint | A constraint that prevents additional exposure without requiring liquidation of existing valid exposure. |
| Execution Math | Any calculation that can directly determine an executable trading action. |
| UNKNOWN | A state in which required information is unavailable, stale, ambiguous, contradictory, or unverifiable. |
| Execution Gate | The single deterministic authority that determines whether an order may be submitted. |
| Protection | Verified protective order/risk protection associated with an open position. |
| Reserve | Capital intentionally isolated from normal Core deployment. |
| Good Loss | A loss resulting from correct execution of the approved process. |
| Bad Win | A profitable result produced despite incorrect process execution. |
| SPEC_DEFECT | A contradiction, ambiguity, or missing requirement in the approved specification discovered during implementation. |

---

# 4. Users / Actors

| Actor | Role | Needs / Responsibilities | Access Level |
|---|---|---|---|
| Owner | Human system owner | Approves production changes, controls autonomous operation, reviews research | Admin |
| Trader | Production deterministic system | Executes approved strategy | Automated |
| Guardian | Production safety system | Controls risk, capital, execution, and safety boundaries | Automated |
| Scientist | AI research system | Observes, researches, tests, journals, proposes | Research only |
| Broker | External execution authority | Holds authoritative account/order/position state | External |
| Observer | Human/operator | Views system state, trades, research, and audit information | Read |

---

# 5. Scope

## 5.1 In Scope

- Autonomous Core trend-following execution.
- Deterministic entry and exit logic.
- Deterministic position sizing.
- Deterministic invalidation.
- Deterministic pyramiding.
- Portfolio risk controls.
- Concentration controls on new exposure.
- Capital pool management.
- Independent Black Swan protection.
- Execution Gate.
- Broker reconciliation.
- Protective-order verification.
- Emergency stop/liquidation.
- Append-only trading journal.
- AI research and experimentation.
- Human approval of production changes.
- Auditability and reproducibility.

## 5.2 Out of Scope

- LLM-controlled trading.
- Discretionary Agent trading.
- Agent-generated executable mathematics.
- Automatic production strategy modification.
- Automatic averaging down.
- Fixed-profit-target Core exits.
- Continuous self-rewriting trading behavior.
- Unbounded optimization.
- Building a general-purpose AI trading platform before the Core system is validated.

## 5.3 Explicit Non-Goals

TOSO is not intended to:

- predict every market move;
- maximize the number of winning trades;
- eliminate all losses;
- react to every short-term market movement;
- optimize every portfolio metric simultaneously;
- make the system more complex merely because additional complexity is possible.

---

# 6. Assumptions

| Assumption | Confidence | Impact if Wrong |
|---|---|---|
| Broker state is authoritative for actual account/order/position state. | High | High |
| Market data can become stale or unavailable. | High | High |
| Orders may produce ambiguous outcomes. | High | High |
| Trend-following produces losing trades as part of its normal operation. | High | High |
| Large winners are necessary to produce the intended payoff asymmetry. | High | High |
| Black Swan protection is a separate function from Core trend following. | High | High |
| Production strategy changes require human approval. | High | High |

---

# 7. Inputs

| Input | Source | Format | Required? | Constraints |
|---|---|---|---|---|
| Market data | Approved market-data source | Structured | Yes | Freshness required |
| Account state | Broker | Structured | Yes | Broker authoritative |
| Position state | Broker/internal reconciliation | Structured | Yes | Must be reconciled |
| Order state | Broker | Structured | Yes | Must be reconciled |
| Strategy configuration | Versioned configuration | Structured | Yes | Approved version only |
| Portfolio state | Deterministic system | Structured | Yes | Current/reconciled |
| Research data | Journal/market/trade history | Structured | Scientist | Reproducible |
| Human approvals | Owner | Structured | Where required | Explicit |

---

# 8. Outputs

| Output | Consumer | Required Behavior |
|---|---|---|
| Trading signal | Trader/Guardian | Deterministic interpretation |
| Position-size calculation | Guardian | Deterministic |
| Invalidation | Guardian/Trader | Established before execution |
| Execution decision | Execution system | Gate-controlled |
| Broker order | Broker | Only through authorized execution path |
| Reconciliation state | Guardian | Reflect authoritative broker state |
| Position journal | Scientist/Owner | Append-only |
| Research result | Scientist/Owner | Reproducible |
| Production-change proposal | Owner | Requires human approval |

---

# 9. Functional Requirements

## FR-001 — Deterministic Core Trading

**Requirement**

TOSO shall execute Core trend-following behavior according to an approved, versioned strategy configuration.

**Behavior**

The Core Trader shall determine whether a trade satisfies the approved strategy conditions.

The Agent shall not modify those conditions during execution.

---

## FR-002 — Deterministic Risk and Sizing

**Requirement**

All executable risk calculations shall be performed by deterministic system components.

**Behavior**

The deterministic system owns:

- position sizing;
- trade risk;
- portfolio risk;
- exposure;
- leverage;
- margin;
- concentration;
- allocation;
- correlation;
- liquidity constraints;
- drawdown calculations;
- order quantity;
- Black Swan sizing where applicable.

An Agent-originated value must not directly determine executable mathematics.

---

## FR-003 — Entry Invalidation

**Requirement**

Every Core position shall have an objectively defined, deterministic invalidation established before execution.

**Behavior**

The invalidation must be recorded with the trade and remain governed by the approved strategy/configuration.

The invalidation mechanism must be deterministic, reproducible, and independently calculable without LLM interpretation.

### Gap and Slippage Risk

An invalidation identifies when a trade is considered invalid; it does not guarantee the execution price or maximum realized loss.

Position sizing and portfolio-risk controls must account for the possibility of adverse gaps and execution slippage beyond the invalidation level.

The implementation must demonstrate that gap/slippage risk has been explicitly considered rather than assuming the invalidation price is a guaranteed cash stop.

---

## FR-004 — Invalidation Exit

**Requirement**

When a Core position reaches its deterministic invalidation, TOSO shall initiate the required exit.

**Behavior**

The system shall not:

- delay solely for discretionary reasons;
- ask the Agent whether the thesis should continue;
- rewrite the invalidation;
- average down;
- add exposure.

An overnight or intraday gap beyond the invalidation does not invalidate the exit requirement; it changes the realized execution price and therefore the realized loss.

---

## FR-005 — Winner Pyramiding

**Requirement**

TOSO may add exposure to an existing Core position only when the position satisfies an objectively defined, deterministic winner/strength condition contained in the approved strategy configuration.

The winner/strength condition must be:

- deterministic;
- versioned;
- reproducible;
- independently calculable;
- free of LLM interpretation at execution time.

**Behavior**

A pyramid must:

- be associated with an existing valid position;
- satisfy the configured winner/strength condition;
- satisfy all applicable strategy conditions;
- pass deterministic risk calculations;
- evaluate the resulting aggregate position and portfolio state;
- satisfy concentration and capital constraints;
- satisfy broker constraints;
- pass the Execution Gate.

A position cannot qualify for additional exposure solely because it has declined in price, because additional exposure would improve its average entry price, or because the Agent believes the position will recover.

The specific mathematical definition of winner/strength belongs in the approved versioned strategy configuration rather than this root specification.

---

## FR-006 — No Averaging Down

**Requirement**

TOSO shall prohibit additional exposure intended to rescue or recover a losing position.

**Behavior**

A declining position cannot become eligible for pyramiding merely because its price has fallen.

---

## FR-007 — Let Winners Run

**Requirement**

TOSO shall not reduce a valid Core position solely because it is profitable, has appreciated substantially, or has become a large portfolio weight.

**Behavior**

Valid winners remain eligible for continued exposure until their deterministic exit or an independent safety condition requires action.

Concentration controls new exposure; it does not create a normal exit condition.

---

## FR-008 — Concentration Intake Constraint

**Requirement**

Concentration limits shall restrict new exposure without automatically forcing liquidation of valid existing positions.

**Behavior**

When concentration is exceeded or reached:

- new exposure may be blocked;
- pyramiding may be blocked;
- unrelated valid positions are not automatically liquidated;
- normal strategy invalidations remain authoritative.

---

## FR-009 — Portfolio Risk

**Requirement**

Before new exposure is authorized, deterministic portfolio-risk controls shall evaluate the proposed action against approved limits.

These controls may include:

- per-trade risk;
- gross exposure;
- long/short exposure;
- leverage;
- margin;
- concentration;
- sector exposure;
- correlation;
- liquidity;
- drawdown;
- capital availability;
- strategy allocation.

### Aggregate Position Risk

Every pyramiding decision must evaluate the **resulting aggregate position and portfolio state**, not merely the incremental order.

The proposed addition must remain within all applicable trade-risk, portfolio-risk, concentration, leverage, liquidity, and capital constraints.

A profitable existing position does not by itself authorize additional risk.

---

## FR-010 — Capital Separation

**Requirement**

TOSO shall maintain separate capital functions for Core, Reserve, and Black Swan protection.

**Behavior**

Reserve capital shall not be silently consumed by normal Core trading.

Black Swan capital shall not be silently repurposed as normal Core capital.

---

## FR-011 — Core / Black Swan Independence

**Requirement**

Black Swan protection shall remain an independent system from Core trend following.

**Behavior**

Black Swan logic shall not silently modify:

- Core entries;
- Core invalidations;
- Core pyramiding rules;
- Core strategy calculations.

Black Swan may independently act when its own approved conditions require defensive action.

---

## FR-012 — Execution Gate

**Requirement**

Every executable order shall pass through one deterministic Execution Gate.

**Behavior**

The Gate must establish that all required information and controls are valid before allowing submission.

The Gate should organize required checks into three functional domains:

### 1. System & Feed Check

- System operational?
- Broker connected?
- Required market/account data fresh?
- Emergency Stop inactive?
- No unresolved system state that prevents execution?

### 2. Intake & Risk Check

- Approved strategy/configuration valid?
- Position state valid?
- If adding: position satisfies deterministic winner condition?
- Concentration permits additional exposure?
- Portfolio risk permits exposure?
- Capital/buying power available?
- Required deterministic calculations valid?

### 3. Execution Safety Check

- Order identity valid and unique?
- No conflicting order in flight?
- Broker constraints satisfied?
- Required protection conditions satisfied?
- No unresolved reconciliation state?
- No other blocking safety condition present?

The implementation may internally divide or combine these checks, but no required control domain may be bypassed.

If a required check is UNKNOWN, execution is rejected or held pending resolution.

---

## FR-013 — Broker Authority and Reconciliation

**Requirement**

Broker state shall be treated as authoritative for actual orders, fills, positions, and account state.

**Behavior**

Ambiguous order outcomes shall be reconciled before a new conflicting action is submitted.

TOSO shall never blindly retry an ambiguous order.

---

## FR-014 — Protection Contract

**Requirement**

Every open position requiring protective control shall have verified protection.

**Behavior**

If protection is missing, stale, ambiguous, or lost:

1. prevent new exposure affecting the position;
2. attempt deterministic recovery;
3. if protection cannot be restored, execute the approved liquidation/safety response.

The implementation may use an internal state machine, but the root requirement is the protection contract above.

Protection does not guarantee a fill at the protected price during a gap or other adverse execution condition.

---

## FR-015 — Emergency Stop

**Requirement**

TOSO shall provide a deterministic emergency mechanism capable of stopping new autonomous trading and liquidating positions when required.

**Behavior**

Emergency Stop is distinct from ordinary Pause.

A Pause prevents new exposure while continuing normal management of existing positions.

Emergency Stop may terminate autonomous trading and initiate emergency liquidation according to approved rules.

---

## FR-016 — Journal

**Requirement**

TOSO shall maintain an append-only record of trading activity and material system decisions.

At minimum, records shall support reconstruction of:

- strategy/configuration version;
- signal;
- calculation versions;
- risk decision;
- allocation decision;
- execution decision;
- broker result;
- fills;
- position changes;
- invalidation;
- pyramid events;
- failures;
- emergency events.

---

## FR-017 — Scientist Research

**Requirement**

The Scientist shall provide a controlled research process for learning from TOSO's behavior and results.

**Research loop**

```text
Observation
    ↓
Question
    ↓
Hypothesis
    ↓
Evidence
    ↓
Experiment
    ↓
Backtest
    ↓
Out-of-Sample Evaluation
    ↓
Paper Validation
    ↓
Evaluation
    ↓
Human Approval
    ↓
Versioned Production Change
```

The Scientist may propose.

The Owner approves.

The Trader executes only the approved version.

---

## FR-018 — Research Integrity

**Requirement**

Research must distinguish discovery from validation.

Experiments shall use appropriate separation between development and held-out evaluation data.

The Scientist shall not repeatedly search until a desired result is found and then present that result as independent validation.

Research must remain reproducible.

The initial implementation need not prescribe a particular experiment-registry architecture, but research must preserve the evidence, dataset/configuration versions, methodology, and results necessary to reproduce and evaluate material conclusions.

---

## FR-019 — Human Approval

**Requirement**

Production strategy or risk-behavior changes require explicit human approval.

**Behavior**

The Scientist cannot promote its own proposal into production.

---

## FR-020 — Short Position Safety

**Requirement**

Short positions shall remain subject to broker availability, borrow, locate, and other applicable execution constraints.

**Behavior**

If required short-sale information is unavailable or invalid, the affected action shall not execute.

Specific borrow economics may be added through approved configuration once defined.

---

# 10. Behavioral Rules / Invariants

These are the permanent rules of TOSO.

## INV-001 — Add to Winners

TOSO may add exposure only to positions satisfying deterministic winner/pyramiding conditions.

## INV-002 — Let Winners Run

A valid winner shall not be exited solely because it has become profitable, large, or highly appreciated.

## INV-003 — Never Average Down

TOSO shall never add exposure to rescue a losing position.

## INV-004 — Never Add Risk to Losers

Loss status cannot be converted into eligibility for additional risk.

## INV-005 — Cut Invalidations

A reached Core invalidation requires the prescribed exit behavior.

## INV-006 — Deterministic Execution Math

Executable trading mathematics shall never be delegated to an LLM/Agent.

## INV-007 — One Execution Authority

No trading order may bypass the Execution Gate.

## INV-008 — UNKNOWN Is Not Safe

Required information that is UNKNOWN shall not be treated as valid merely because execution is desirable.

## INV-009 — Broker Authority

When local state conflicts with confirmed broker state, broker state governs actual account/order/position truth.

## INV-010 — No Blind Retry

Ambiguous broker outcomes must be reconciled before conflicting retry/action.

## INV-011 — Protection

Every required open-position protection must be verified.

## INV-012 — Reserve Isolation

Reserve capital cannot be silently consumed by ordinary Core trading.

## INV-013 — Core / Black Swan Independence

Black Swan protection and Core strategy remain separate systems.

## INV-014 — No Autonomous Production Change

The Scientist cannot independently change production behavior.

## INV-015 — Human Approval

Production changes require explicit Owner approval.

## INV-016 — Append-Only Journal

Material trading and execution history cannot be silently rewritten.

## INV-017 — Reproducibility

Approved production behavior and research conclusions must be reproducible from recorded inputs/configuration/version information.

## INV-018 — Good Loss / Bad Win

A profitable outcome does not excuse incorrect process execution.

A losing outcome does not imply the process was incorrect.

## INV-019 — Research, Not Reflex

A surprising event may generate research; it does not automatically change production behavior.

## INV-020 — Simplicity

TOSO shall not introduce a permanent rule, state, component, or governance mechanism unless it:

- protects a permanent invariant;
- prevents a demonstrated material failure;
- is required for deterministic execution or safety;
- is necessary to make an approved requirement testable.

When equivalent protections exist, the simpler mechanism is preferred.

---

# 11. Failure, Recovery, and Fallback Behavior

## 11.1 Failure Rules

| Failure | Detection | Required Response | Recovery | State |
|---|---|---|---|---|
| Market data stale | Freshness check | Block affected execution | Restore valid data | DEGRADED |
| Market data unavailable | Data service | Block affected execution | Restore data | DEGRADED |
| Risk calculation unavailable | Calculation service | Reject order | Restore deterministic calculation | DEGRADED |
| Gate unavailable | Gate health | Reject order | Restore Gate | HALTED/DEGRADED |
| Broker unavailable | Broker connectivity | No new conflicting orders | Reconcile after recovery | RECONCILING |
| Order outcome ambiguous | Broker reconciliation | Do not retry blindly | Reconcile broker state | RECONCILING |
| Position protection missing | Protection check | Block affected new exposure | Restore protection | DEGRADED |
| Protection cannot be restored | Recovery failure | Execute approved safety response | Human/system recovery | HALTED |
| Required input UNKNOWN | Validation | Reject/hold action | Resolve uncertainty | DEGRADED |
| Configuration invalid | Config validation | No execution | Restore approved config | HALTED |
| Unauthorized Agent action | Boundary violation | Reject and record | Human review | HALTED/DEGRADED |
| Emergency condition | Emergency control | Stop and/or liquidate | Human-controlled recovery | HALTED |

## 11.2 Explicit System States

```text
AUTONOMOUS
    ↓
PAUSED
    ↓
DEGRADED
    ↓
RECONCILING
    ↓
HALTED
```

Transitions must be deterministic and auditable.

## 11.3 Error Taxonomy

| Category | Required Handling |
|---|---|
| Data | Fail closed for affected decisions |
| Calculation | No executable action without deterministic result |
| Broker / Integration | Reconcile authoritative state |
| Internal | Fail safely and record |
| Security | Reject unauthorized action and record |
| Configuration | Do not execute unapproved configuration |
| Research | Isolate from production execution |

## 11.4 Fallback Rules

There is no discretionary fallback that changes Core trading behavior.

When the preferred path is unavailable, the system shall prefer:

1. safe continuation of already-valid positions where possible;
2. deterministic protective management;
3. blocking new exposure;
4. reconciliation;
5. human intervention where required.

A fallback must not silently change strategy behavior.

## 11.5 Retry Rules

Only operations known to be safely retryable may be retried.

Order submission must be protected by deterministic client/order identity and broker reconciliation.

No retry may create duplicate exposure.

## 11.6 Idempotency

All executable operations must have sufficient identity/state controls to prevent duplicate trading effects.

---

# 12. Guardrails

## Content Boundaries

The Scientist may produce analysis, research, hypotheses, and recommendations.

It must not produce an executable instruction that bypasses deterministic validation.

## Tool-Use Permissions

The Scientist may access approved research data, journal data, documentation, and research tooling.

It shall not possess or use broker credentials for autonomous trading.

## Escalation on Uncertainty

When required information is uncertain:

- research may continue;
- production execution does not.

UNKNOWN must be surfaced rather than silently converted to a valid value.

## Autonomous Action Limits

The Scientist has no autonomous authority to:

- submit orders;
- cancel orders;
- change production risk;
- change production strategy;
- change capital allocations;
- disable safeguards.

---

# 13. Memory

## Short-Term / Session Memory

Research sessions may maintain context necessary to conduct a specific experiment or investigation.

## Long-Term / Persisted Memory

The system shall persist material research conclusions, experiment results, strategy versions, and approved changes necessary for reproducibility.

## Scope and Isolation

Research findings must remain distinguishable from approved production configuration.

An unapproved research conclusion must never be interpreted as production policy.

---

# 14. Security / Data Requirements

## Authentication

Production trading credentials must be restricted to the minimum components required for broker interaction.

## Authorization

Only authorized execution components may submit/cancel orders.

Only the Owner may approve production strategy/risk changes or emergency recovery where human control is required.

## Data Protection

Sensitive credentials and account information must be protected.

## Secrets

The Scientist must not have broker credentials.

## Audit / Traceability

Material execution, configuration, research, and approval events must be traceable.

## Privacy / Compliance

The implementation shall comply with applicable organizational, broker, and regulatory requirements.

---

# 15. Performance / Reliability Requirements

TOSO prioritizes **correctness and safety over execution speed**.

| Requirement | Target |
|---|---|
| Risk calculation | Deterministic and reproducible |
| Gate decision | Deterministic |
| Duplicate order prevention | Required |
| Broker reconciliation | Required after ambiguity/disconnect |
| Protection verification | Required for applicable open positions |
| Journal integrity | Append-only |
| Research reproducibility | Required |
| Recovery behavior | Deterministic |

Exact numerical latency/availability targets shall be defined when implementation requirements are established.

---

# 16. Observability & Telemetry

## System Observability

A human must be able to determine from system records:

- whether TOSO is autonomous;
- whether it is paused/degraded/halted/reconciling;
- what positions exist;
- why a trade was entered;
- why additional exposure was or was not permitted;
- why a position exited;
- whether protection exists;
- what the Gate decided;
- what the broker reported;
- whether any state is UNKNOWN;
- what strategy/configuration version was active.

## Product Telemetry

TOSO shall capture material trading and research events.

## Feedback Loops

Trade results and process outcomes shall feed the Scientist's research process.

They shall not directly alter production behavior.

---

# 17. Integration Requirements

| Integration | Purpose | Required Behavior | Failure Behavior |
|---|---|---|---|
| Market Data | Trading/research input | Fresh, validated data | Block affected execution |
| Broker | Execution/account authority | Reconciled state | Reconcile / halt as required |
| Research Storage | Journal/research | Persistent, traceable | Research degraded; trading safety preserved |
| Configuration | Approved behavior | Versioned/validated | Block execution |
| Notification/Monitoring | Operator awareness | Surface material events | Preserve safety state |

---

# 18. Constraints

## Technical

- Deterministic execution math.
- Explicit system boundaries.
- Reproducible behavior.
- Broker reconciliation.
- Safe handling of UNKNOWN.

## Business

- System should pursue long-term compounding rather than short-term activity.
- Complexity must justify itself.

## Regulatory / Security

- Production credentials must be isolated.
- Trading actions must be auditable.
- Applicable trading constraints must be enforced.

## Operational

- Autonomous operation must fail safely.
- Human intervention must remain available.

## Budget / Time

Implementation should prioritize the deterministic trading core and safety boundary before advanced research capabilities.

## Portability

Implementation should avoid unnecessary coupling between strategy requirements and specific vendors, models, IDEs, or runtime technologies.

---

# 19. Dependencies

| Dependency | Type | Required For | Risk |
|---|---|---|---|
| Broker | External | Live execution | High |
| Market data | External | Strategy | High |
| Deterministic calculation layer | Internal | Risk/execution | High |
| Configuration | Internal | Approved behavior | High |
| Journal | Internal | Audit/research | Medium |
| Research environment | Internal | Scientist | Medium |

---

# 20. Risks

## RISK-001 — Autonomous Trading Loss

**Impact:** High  
**Likelihood:** High

**Mitigation:** Deterministic risk, invalidation, Gate, reconciliation, and emergency controls.

**Verification:** Adversarial tests and paper/live validation.

---

## RISK-002 — Agent Boundary Failure

**Impact:** Critical  
**Likelihood:** Medium

**Mitigation:** Hard technical separation between research and execution.

**Verification:** Security and integration tests demonstrating Agent-originated executable values cannot reach execution.

---

## RISK-003 — Overfitting

**Impact:** High  
**Likelihood:** High

**Mitigation:** Held-out evaluation, reproducibility, controlled experimentation, human approval.

**Verification:** Research acceptance criteria.

---

## RISK-004 — Correlated Core Exposure

**Impact:** High  
**Likelihood:** Medium

**Mitigation:** Portfolio risk, concentration, correlation, liquidity, and exposure controls.

---

## RISK-005 — Black Swan Drag

**Impact:** Medium  
**Likelihood:** High

**Mitigation:** Keep defensive capital independent and evaluate carry/drag empirically.

---

## RISK-006 — Broker Divergence

**Impact:** Critical  
**Likelihood:** Medium

**Mitigation:** Broker-authoritative reconciliation and no blind retries.

---

## RISK-007 — Overnight Gap

**Impact:** High  
**Likelihood:** Medium

**Mitigation:** Recognize that an invalidation price is not a guaranteed fill price or maximum realized loss.

Position sizing and portfolio-risk controls must explicitly account for adverse gaps and slippage.

---

## RISK-008 — Complexity Drift

**Impact:** High  
**Likelihood:** High

**Mitigation:** INV-020 Complexity Principle.

---

# 21. Phases

## Phase 1 — Deterministic Trader

**Objective**

Establish the Core trading behavior.

**Stories**

- TRADER-001 — Strategy configuration
- TRADER-002 — Deterministic signal
- TRADER-003 — Entry
- TRADER-004 — Invalidation
- TRADER-005 — Position sizing
- TRADER-006 — Winner pyramiding
- TRADER-007 — Winner continuation

**Exit Conditions**

- Core behavior is deterministic.
- No averaging down is technically possible.
- Invalidations are enforced.
- Winner continuation is enforced.
- Pyramiding is winner-only.
- Aggregate position risk is evaluated after every proposed pyramid.
- Gap/slippage risk is explicitly incorporated into risk design.

---

## Phase 2 — Guardian

**Objective**

Build the safety boundary around the Trader.

**Stories**

- GUARD-001 — Portfolio risk
- GUARD-002 — Concentration intake
- GUARD-003 — Capital separation
- GUARD-004 — Execution Gate
- GUARD-005 — Protection
- GUARD-006 — Emergency Stop

**Exit Conditions**

- No order bypasses the Gate.
- UNKNOWN blocks required execution.
- Required protection is verified.
- Reserve is isolated.
- Emergency control is tested.

---

## Phase 3 — Broker Integrity

**Objective**

Make broker state authoritative and safely reconciled.

**Stories**

- BROKER-001 — Account reconciliation
- BROKER-002 — Order reconciliation
- BROKER-003 — Position reconciliation
- BROKER-004 — Ambiguous outcome handling
- BROKER-005 — Idempotent order identity

**Exit Conditions**

- Broker state can be reconciled.
- Ambiguous orders cannot cause blind duplicate exposure.
- Local state cannot silently override broker truth.

---

## Phase 4 — Black Swan

**Objective**

Implement independent portfolio defense.

**Stories**

- BS-001 — Black Swan capital
- BS-002 — Defensive instruments
- BS-003 — Defensive sizing
- BS-004 — Defensive exits/de-escalation

**Exit Conditions**

- Black Swan remains independent of Core.
- Defensive behavior is deterministic.
- Capital is separately controlled.

Exact instruments and parameters are defined before implementation.

---

## Phase 5 — Scientist

**Objective**

Create the research layer without granting execution authority.

**Stories**

- SCI-001 — Observation
- SCI-002 — Research journal
- SCI-003 — Hypothesis
- SCI-004 — Experiment
- SCI-005 — Backtest
- SCI-006 — OOS evaluation
- SCI-007 — Production proposal

**Exit Conditions**

- Scientist cannot execute.
- Research is reproducible.
- OOS evaluation exists.
- Production changes require human approval.

---

## Phase 6 — Paper Validation

**Objective**

Validate complete system behavior without live capital risk.

**Exit Conditions**

- Core behavior verified.
- Guardian verified.
- Broker integration verified.
- Research boundary verified.
- Failure paths tested.
- Journal verified.
- Emergency behavior verified.

---

## Phase 7 — Live Readiness

**Objective**

Demonstrate that the complete system is safe enough for controlled live operation.

**Exit Conditions**

- All blocking Open Questions resolved.
- All required stories verified.
- Acceptance Matrix complete.
- No unresolved critical safety findings.
- Human approval obtained.

---

# 22. Stories

Stories shall remain implementation-sized and traceable to requirements.

## Story TRADER-001 — Deterministic Core Configuration

**Objective**

Create the approved configuration required for deterministic Core strategy behavior.

**Risk:** HIGH

**Cross-Cutting Requirements Touched**

- §10 Failure/Error
- §15 Observability

### Acceptance Criteria

#### AC-001

Only an approved, versioned configuration may control production Core behavior.

**Verification:** Configuration validation test.

#### AC-002

Invalid or unavailable configuration prevents execution.

**Verification:** Failure-path test.

---

## Story TRADER-002 — Winner-Only Pyramiding

**Objective**

Allow additional exposure only when deterministic winner conditions are satisfied.

**Risk:** HIGH

### Acceptance Criteria

#### AC-001

A losing position cannot receive additional exposure.

**Verification:** Automated test covering declining-position scenarios.

#### AC-002

A qualifying winner can receive additional exposure when all risk and Gate requirements pass.

**Verification:** Integration test.

#### AC-003

Concentration can block additional exposure without forcing liquidation of the valid winner.

**Verification:** Portfolio-risk test.

#### AC-004

The winner/strength condition is explicit, deterministic, versioned, reproducible, and independently calculable without LLM interpretation.

**Verification:** Configuration/schema validation and deterministic calculation test.

#### AC-005

The proposed pyramid is evaluated against the resulting aggregate position and portfolio state.

**Verification:** Test demonstrating that an otherwise valid incremental order is rejected when the resulting aggregate position violates an applicable risk constraint.

---

## Story TRADER-003 — Invalidation Enforcement

**Objective**

Ensure invalidated Core positions are exited without discretionary override.

**Risk:** CRITICAL

### Acceptance Criteria

#### AC-001

Every Core position has a recorded deterministic invalidation before execution.

**Verification:** Entry validation test.

#### AC-002

Reaching invalidation causes the required exit process.

**Verification:** Boundary-condition test.

#### AC-003

Agent input cannot delay or override invalidation.

**Verification:** Agent-boundary integration test.

#### AC-004

The risk design explicitly accounts for adverse gaps/slippage beyond the invalidation price.

**Verification:** Risk-model test and documented scenario analysis.

---

## Story GUARD-001 — Execution Gate

**Objective**

Create the sole execution authority.

**Risk:** CRITICAL

### Acceptance Criteria

#### AC-001

Every executable order passes through the Gate.

**Verification:** Architectural/integration test.

#### AC-002

Required UNKNOWN inputs cause rejection or hold.

**Verification:** Failure injection test.

#### AC-003

A rejected Gate decision cannot reach the broker.

**Verification:** Integration test.

#### AC-004

Gate evaluation covers the three required functional domains: System & Feed, Intake & Risk, and Execution Safety.

**Verification:** Gate test suite and traceability evidence.

---

## Story BROKER-001 — Reconciliation

**Objective**

Ensure actual broker state is authoritative.

**Risk:** CRITICAL

### Acceptance Criteria

#### AC-001

Order state can be reconciled after an ambiguous response.

**Verification:** Simulated broker ambiguity.

#### AC-002

The system does not blindly retry an ambiguous order.

**Verification:** Failure-path test.

#### AC-003

Reconciled broker state is reflected in internal state.

**Verification:** Integration test.

---

## Story GUARD-005 — Protection Contract

**Objective**

Ensure every applicable open position has verified protection and that protection failures are handled deterministically.

**Risk:** CRITICAL

### Acceptance Criteria

#### AC-001

An open position without verified protection cannot receive additional exposure.

**Verification:** Protection-state integration test.

#### AC-002

The system attempts deterministic protection recovery when protection is missing or lost.

**Verification:** Failure injection test.

#### AC-003

If protection cannot be restored, the prescribed safety/liquidation response is initiated.

**Verification:** Recovery-failure test.

#### AC-004

The implementation does not represent the protection price as a guaranteed maximum realized loss during gap/slippage conditions.

**Verification:** Adverse-gap test.

---

## Story SCI-001 — Scientist Boundary

**Objective**

Guarantee that research intelligence cannot execute trades.

**Risk:** CRITICAL

### Acceptance Criteria

#### AC-001

Scientist tools cannot submit or cancel production orders.

**Verification:** Permission/security test.

#### AC-002

Scientist-generated executable values cannot bypass deterministic calculation.

**Verification:** Adversarial integration test.

#### AC-003

Production changes require explicit human approval.

**Verification:** Approval workflow test.

---

# 23. Testing & Evals Strategy

## Testing Strategy

| Test Type | Required? | Coverage |
|---|---|---|
| Unit | Yes | Deterministic calculations and rules |
| Integration | Yes | Trader/Guardian/Broker boundaries |
| End-to-end | Yes | Full execution flow |
| Failure injection | Yes | Broker/data/protection/UNKNOWN |
| Security | Yes | Agent and credential boundaries |
| Regression | Yes | Permanent invariants |
| Paper trading | Yes | Complete system behavior |

## Required Invariant Testing

The test suite must explicitly demonstrate:

- no averaging down;
- no risk added to losers;
- winner-only pyramiding;
- winners are not trimmed solely due to appreciation;
- invalidation exits;
- concentration blocks new exposure rather than forcing valid-winner liquidation;
- Agent cannot execute;
- Gate cannot be bypassed;
- UNKNOWN fails closed;
- broker ambiguity does not produce blind retry;
- Reserve cannot be silently consumed;
- Black Swan remains independent;
- aggregate position risk is evaluated after pyramiding;
- adverse gap/slippage scenarios do not assume guaranteed execution at invalidation.

## Evals

The Scientist's outputs should be evaluated for:

- research quality;
- hypothesis quality;
- evidence quality;
- reproducibility;
- proper distinction between observation and conclusion;
- appropriate uncertainty;
- no unsupported production recommendation.

Research evals do not grant execution authority.

---

# 24. Non-Functional Requirements

## Maintainability

The system should remain understandable enough that a human can determine:

> **Why did TOSO do this?**

without reconstructing opaque Agent reasoning.

## Reliability

Safety-critical behavior must be deterministic.

## Scalability

Scale should not require weakening safety boundaries or introducing Agent control into execution.

## Auditability

A completed trade should be reconstructable from recorded system state, configuration, calculations, decisions, and broker results.

---

# 25. Domain Standards

Applicable standards include:

- applicable broker/API requirements;
- applicable security standards;
- applicable financial/trading controls;
- project engineering standards.

Specific implementation standards belong outside this root specification.

---

# 26. Tooling / Repository Profile

The specification does not prescribe:

- IDE;
- programming language;
- Agent framework;
- LLM provider;
- deployment platform;
- database;
- specific observability tool.

Those belong in the implementation/tooling profile unless explicitly elevated to a project requirement.

---

# 27. Definition of Done

TOSO is complete only when:

- all required stories are verified;
- all permanent invariants are technically enforced;
- Core Trader behavior is deterministic;
- winner/pyramiding conditions are explicit and deterministic;
- aggregate position risk is evaluated after pyramiding;
- gap/slippage risk is explicitly accounted for;
- Guardian controls are enforced;
- Execution Gate cannot be bypassed;
- broker reconciliation is verified;
- required protection is verified;
- emergency controls are tested;
- Reserve and Black Swan boundaries are verified;
- Scientist execution isolation is verified;
- research integrity is demonstrated;
- production changes require human approval;
- journal/auditability requirements are satisfied;
- paper validation passes;
- required security/regression/failure testing passes;
- Acceptance Matrix contains evidence for applicable requirements;
- no unresolved blocking findings remain;
- no unresolved blocking Open Questions remain;
- final approval is recorded.

---

# 28. Acceptance Matrix

| Requirement | Story | Acceptance Criterion | Evidence Required | Status |
|---|---|---|---|---|
| FR-003 | TRADER-003 | AC-001/004 | Entry/risk test | DRAFT |
| FR-004 | TRADER-003 | AC-002 | Invalidation test | DRAFT |
| FR-005 | TRADER-002 | AC-001/002/004/005 | Pyramiding tests | DRAFT |
| FR-006 | TRADER-002 | AC-001 | Test evidence | DRAFT |
| FR-007 | TRADER-002 | AC-003 | Test evidence | DRAFT |
| FR-009 | TRADER-002 | AC-005 | Portfolio-risk test | DRAFT |
| FR-012 | GUARD-001 | AC-001/002/003/004 | Integration evidence | DRAFT |
| FR-013 | BROKER-001 | AC-001/002/003 | Broker simulation | DRAFT |
| FR-014 | GUARD-005 | AC-001/002/003/004 | Protection tests | DRAFT |
| FR-015 | GUARD-006 | TBD | Emergency test | DRAFT |
| FR-017 | SCI-001 | AC-001/002/003 | Security/integration evidence | DRAFT |
| INV-001 | TRADER-002 | AC-001/002/004 | Test evidence | DRAFT |
| INV-002 | TRADER-002 | AC-003 | Test evidence | DRAFT |
| INV-003 | TRADER-002 | AC-001 | Test evidence | DRAFT |
| INV-004 | TRADER-002 | AC-001 | Test evidence | DRAFT |
| INV-005 | TRADER-003 | AC-002 | Test evidence | DRAFT |
| INV-006 | SCI-001 | AC-002 | Boundary test | DRAFT |
| INV-007 | GUARD-001 | AC-001 | Architecture/integration evidence | DRAFT |
| INV-008 | GUARD-001 | AC-002 | Failure injection | DRAFT |
| INV-009 | BROKER-001 | AC-003 | Integration evidence | DRAFT |
| INV-010 | BROKER-001 | AC-002 | Failure injection | DRAFT |
| INV-014 | SCI-001 | AC-003 | Approval test | DRAFT |

The matrix shall be expanded as stories are finalized.

---

# 29. Open Questions

Only unresolved questions that materially affect architecture, safety, or initial implementation are blocking.

| Question | Blocking? | Owner |
|---|---|---|
| What exact Core risk parameters apply? | Yes | Owner |
| What exact concentration thresholds/bands should Core use? | Yes | Owner |
| What exact Core signal/bar frequency should be used? | Yes | Owner |
| What exact deterministic winner/strength condition should the initial Core strategy use? | Yes | Owner |
| What exact pyramiding risk/spacing rules should the initial Core strategy use? | Yes | Owner |
| What exact Black Swan instruments are approved? | Yes | Owner |
| What exact Black Swan allocation/sizing parameters apply? | Yes | Owner |
| What exact short-sale/borrow rules apply? | Yes for live shorts | Owner |
| What exact universe/eligibility rules apply? | Yes | Owner |
| What tax-lot/wash-sale behavior is required? | No for initial architecture | Owner |
| What market-data redundancy is required? | No for initial architecture | Owner |

---

# 30. Approval

The specification is not executable until explicitly approved.

```text
Status: APPROVED

Version: 0.5
Approved By:
Approved At:
Approval Notes:
```

---

# 31. Specification Amendment

Once approved, this specification is locked.

A contradiction discovered during implementation is a **SPEC_DEFECT**, not permission for an Agent or Builder to silently alter the specification.

Required process:

```text
Implementation discovers contradiction
        ↓
SPEC_DEFECT recorded
        ↓
Affected story BLOCKED
        ↓
Human reviews specification
        ↓
APPROVE / REJECT amendment
        ↓
If approved:
    increment version
    record change
    reconcile implementation plan
    identify affected stories/tests
        ↓
Affected work becomes eligible
```

## Amendment Record

```text
Amendment ID:
Previous Version:
New Version:
Date:
Requested Change:
Reason:
Affected Requirements:
Affected Phases:
Affected Stories:
Approved By:
Approval Date:
```

---

# 32. Final Notes

## The TOSO Design Principle

TOSO should be **simple in behavior and sophisticated in protection**.

The Trader should be boring.

The Guardian should be strict.

The Scientist should be curious.

They should not become one thing.

### Trader

```text
Find trend
    ↓
Enter
    ↓
Define risk
    ↓
If wrong → Exit
    ↓
If right → Add
    ↓
Let winner run
    ↓
Trend ends → Exit
```

### Guardian

```text
Risk
Capital
Concentration
Protection
Execution
Broker State
Emergency Control
```

The Guardian exists to protect the Trader's behavior, not continuously rewrite it.

### Scientist

```text
Observe
    ↓
Question
    ↓
Hypothesize
    ↓
Experiment
    ↓
Validate
    ↓
Propose
    ↓
Human Approval
    ↓
Versioned Change
```

The Scientist studies the Trader.

It does not become the Trader.

---

## Permanent TOSO Philosophy

> **Add to winners.**
>
> **Let winners run.**
>
> **Never average down.**
>
> **Never add risk to losers.**
>
> **Cut invalidated losses.**
>
> **Learn without improvising.**

These principles define the character of TOSO and must remain intact through future revisions.

Any future requirement that materially changes these principles requires explicit human approval and specification amendment.

---

## Final Architectural Test

Before adding a permanent mechanism to TOSO, ask:

> **Does this protect TOSO's character, or does it create new character?**

If it protects the character, it may belong.

If it prevents a demonstrated material failure, it may belong.

If it is required for deterministic execution or safety, it belongs.

If it merely makes the system more sophisticated, it probably does not.

**The goal is not to build the most complicated autonomous trading system.**

**The goal is to build the most disciplined version of this particular trading idea.**
