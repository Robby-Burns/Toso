# Acceptance Matrix Audit Report
- **Timestamp:** 2026-11-10T22:06:48.384446+00:00
- **Total Requirements:** 40
- **Verified Requirements:** 40 (100%)
- **Matrix Digest (SHA-256):** `4a3d32523699069152a5a769eb1d549e537d8f46124435c9acf820de4d977c4a`

| Requirement | Title | Story | Verified Tests | Status |
|---|---|---|---|:---:|
| **FR-001** | Deterministic Core Trading | TRADER-001 | tests/test_signals.py<br>tests/test_config.py | **VERIFIED** |
| **FR-002** | Deterministic Risk and Sizing | TRADER-005 | tests/test_sizing.py | **VERIFIED** |
| **FR-003** | Entry Invalidation | TRADER-003 | tests/test_entry_invalidation.py | **VERIFIED** |
| **FR-004** | Invalidation Exit | TRADER-004 | tests/test_exit_enforcement.py | **VERIFIED** |
| **FR-005** | Winner Pyramiding | TRADER-006 | tests/test_pyramiding.py | **VERIFIED** |
| **FR-006** | No Averaging Down | TRADER-006 | tests/test_pyramiding.py | **VERIFIED** |
| **FR-007** | Let Winners Run | TRADER-007 | tests/test_continuation.py | **VERIFIED** |
| **FR-008** | Concentration Intake Constraint | GUARD-002 | tests/test_guardian_concentration.py | **VERIFIED** |
| **FR-009** | Portfolio Risk | GUARD-001 | tests/test_guardian_portfolio_risk.py | **VERIFIED** |
| **FR-010** | Capital Separation | GUARD-003 | tests/test_guardian_capital.py<br>tests/test_defense_capital.py | **VERIFIED** |
| **FR-011** | Core / Black Swan Independence | BS-002 | tests/test_defense_universe.py<br>tests/red_team/test_defense_universe_adversarial.py | **VERIFIED** |
| **FR-012** | Execution Gate | GUARD-004 | tests/test_guardian_execution_gate.py | **VERIFIED** |
| **FR-013** | Broker Authority and Reconciliation | BROKER-001 | tests/test_broker_account_reconciliation.py<br>tests/test_broker_order_reconciliation.py<br>tests/test_broker_position_reconciliation.py | **VERIFIED** |
| **FR-014** | Protection Contract | GUARD-005 | tests/test_guardian_protection_contract.py | **VERIFIED** |
| **FR-015** | Emergency Stop & Kill-Switch | PAPER-005 | tests/test_guardian_emergency.py<br>tests/test_paper_kill_switch.py<br>tests/red_team/test_paper_kill_switch_adversarial.py | **VERIFIED** |
| **FR-016** | Trade Journaling | SCI-001 | tests/test_scientist_journal.py<br>tests/test_paper_journal.py<br>tests/red_team/test_paper_journal_adversarial.py | **VERIFIED** |
| **FR-017** | Scientist Research Boundary | SCI-002 | tests/test_scientist_boundary.py | **VERIFIED** |
| **FR-018** | Research Integrity & Hypothesis/OOS | SCI-003 | tests/test_scientist_hypothesis.py<br>tests/test_scientist_experiment.py<br>tests/test_scientist_backtest.py<br>tests/test_scientist_oos.py | **VERIFIED** |
| **FR-019** | Human Approval for Changes | SCI-007 | tests/test_scientist_proposal.py | **VERIFIED** |
| **FR-020** | Short Position Safety | BS-002 | tests/test_defense_universe.py<br>tests/test_live_resolution.py | **VERIFIED** |
| **INV-001** | Add to Winners | TRADER-006 | tests/test_pyramiding.py | **VERIFIED** |
| **INV-002** | Let Winners Run | TRADER-007 | tests/test_continuation.py | **VERIFIED** |
| **INV-003** | Never Average Down | TRADER-006 | tests/test_pyramiding.py | **VERIFIED** |
| **INV-004** | Never Add Risk to Losers | TRADER-006 | tests/test_pyramiding.py | **VERIFIED** |
| **INV-005** | Cut Invalidations | TRADER-004 | tests/test_entry_invalidation.py<br>tests/test_exit_enforcement.py | **VERIFIED** |
| **INV-006** | Deterministic Execution Math | TRADER-005 | tests/test_sizing.py<br>tests/test_defense_sizing.py | **VERIFIED** |
| **INV-007** | One Execution Authority | GUARD-004 | tests/test_guardian_execution_gate.py<br>tests/test_paper_runner.py<br>tests/red_team/test_paper_runner_adversarial.py | **VERIFIED** |
| **INV-008** | UNKNOWN Is Not Safe | GUARD-006 | tests/test_guardian_emergency.py<br>tests/test_paper_kill_switch.py<br>tests/red_team/test_paper_kill_switch_adversarial.py | **VERIFIED** |
| **INV-009** | Broker Authority | BROKER-001 | tests/test_broker_account_reconciliation.py<br>tests/test_broker_position_reconciliation.py | **VERIFIED** |
| **INV-010** | No Blind Retry | BROKER-004 | tests/test_broker_ambiguous_outcomes.py<br>tests/test_paper_kill_switch.py | **VERIFIED** |
| **INV-011** | Protection Contract Enforcement | GUARD-005 | tests/test_guardian_protection_contract.py | **VERIFIED** |
| **INV-012** | Reserve Isolation | GUARD-003 | tests/test_guardian_capital.py | **VERIFIED** |
| **INV-013** | Core / Black Swan Independence | BS-002 | tests/test_defense_universe.py<br>tests/test_live_resolution.py<br>tests/red_team/test_live_resolution_adversarial.py | **VERIFIED** |
| **INV-014** | No Autonomous Production Change | SCI-002 | tests/test_scientist_boundary.py<br>tests/test_scientist_proposal.py | **VERIFIED** |
| **INV-015** | Human Approval | SCI-007 | tests/test_scientist_proposal.py<br>tests/test_config.py | **VERIFIED** |
| **INV-016** | Append-Only Journal | SCI-001 | tests/test_scientist_journal.py<br>tests/test_paper_journal.py | **VERIFIED** |
| **INV-017** | Reproducibility | SCI-005 | tests/test_scientist_backtest.py<br>tests/test_scientist_oos.py | **VERIFIED** |
| **INV-018** | Good Loss / Bad Win | SCI-001 | tests/test_scientist_journal.py | **VERIFIED** |
| **INV-019** | Research, Not Reflex | SCI-002 | tests/test_scientist_boundary.py | **VERIFIED** |
| **INV-020** | Simplicity Invariant | TRADER-001 | tests/test_smoke.py<br>tests/test_signals.py | **VERIFIED** |
