"""TOSO Live Readiness Acceptance Matrix Auditor & Traceability Engine.

Maps and validates all 20 Functional Requirements (FR-001 through FR-020) and
20 Permanent Invariants (INV-001 through INV-020) to automated verification tests,
producing a sealed AcceptanceMatrixAuditReceipt.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from toso.live.errors import IncompleteAcceptanceMatrixError, MissingTraceabilityError
from toso.live.models import (
    AcceptanceMatrixAuditReceipt,
    RequirementCategory,
    RequirementTrace,
)


class AcceptanceMatrixAuditor:
    """Audits and produces authoritative requirement traceability receipts."""

    @classmethod
    def get_canonical_traces(cls) -> tuple[RequirementTrace, ...]:
        """Return the complete canonical mapping of all 40 requirements & invariants."""
        return (
            # Functional Requirements (FR-001 through FR-020)
            RequirementTrace(
                req_id="FR-001",
                title="Deterministic Core Trading",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-001",
                test_files=("tests/test_signals.py", "tests/test_config.py"),
            ),
            RequirementTrace(
                req_id="FR-002",
                title="Deterministic Risk and Sizing",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-005",
                test_files=("tests/test_sizing.py",),
            ),
            RequirementTrace(
                req_id="FR-003",
                title="Entry Invalidation",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-003",
                test_files=("tests/test_entry_invalidation.py",),
            ),
            RequirementTrace(
                req_id="FR-004",
                title="Invalidation Exit",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-004",
                test_files=("tests/test_exit_enforcement.py",),
            ),
            RequirementTrace(
                req_id="FR-005",
                title="Winner Pyramiding",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-006",
                test_files=("tests/test_pyramiding.py",),
            ),
            RequirementTrace(
                req_id="FR-006",
                title="No Averaging Down",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-006",
                test_files=("tests/test_pyramiding.py",),
            ),
            RequirementTrace(
                req_id="FR-007",
                title="Let Winners Run",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="TRADER-007",
                test_files=("tests/test_continuation.py",),
            ),
            RequirementTrace(
                req_id="FR-008",
                title="Concentration Intake Constraint",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="GUARD-002",
                test_files=("tests/test_guardian_concentration.py",),
            ),
            RequirementTrace(
                req_id="FR-009",
                title="Portfolio Risk",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="GUARD-001",
                test_files=("tests/test_guardian_portfolio_risk.py",),
            ),
            RequirementTrace(
                req_id="FR-010",
                title="Capital Separation",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="GUARD-003",
                test_files=("tests/test_guardian_capital.py", "tests/test_defense_capital.py"),
            ),
            RequirementTrace(
                req_id="FR-011",
                title="Core / Black Swan Independence",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="BS-002",
                test_files=(
                    "tests/test_defense_universe.py",
                    "tests/red_team/test_defense_universe_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="FR-012",
                title="Execution Gate",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="GUARD-004",
                test_files=("tests/test_guardian_execution_gate.py",),
            ),
            RequirementTrace(
                req_id="FR-013",
                title="Broker Authority and Reconciliation",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="BROKER-001",
                test_files=(
                    "tests/test_broker_account_reconciliation.py",
                    "tests/test_broker_order_reconciliation.py",
                    "tests/test_broker_position_reconciliation.py",
                ),
            ),
            RequirementTrace(
                req_id="FR-014",
                title="Protection Contract",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="GUARD-005",
                test_files=("tests/test_guardian_protection_contract.py",),
            ),
            RequirementTrace(
                req_id="FR-015",
                title="Emergency Stop & Kill-Switch",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="PAPER-005",
                test_files=(
                    "tests/test_guardian_emergency.py",
                    "tests/test_paper_kill_switch.py",
                    "tests/red_team/test_paper_kill_switch_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="FR-016",
                title="Trade Journaling",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="SCI-001",
                test_files=(
                    "tests/test_scientist_journal.py",
                    "tests/test_paper_journal.py",
                    "tests/red_team/test_paper_journal_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="FR-017",
                title="Scientist Research Boundary",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="SCI-002",
                test_files=("tests/test_scientist_boundary.py",),
            ),
            RequirementTrace(
                req_id="FR-018",
                title="Research Integrity & Hypothesis/OOS",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="SCI-003",
                test_files=(
                    "tests/test_scientist_hypothesis.py",
                    "tests/test_scientist_experiment.py",
                    "tests/test_scientist_backtest.py",
                    "tests/test_scientist_oos.py",
                ),
            ),
            RequirementTrace(
                req_id="FR-019",
                title="Human Approval for Changes",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="SCI-007",
                test_files=("tests/test_scientist_proposal.py",),
            ),
            RequirementTrace(
                req_id="FR-020",
                title="Short Position Safety",
                category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
                primary_story="BS-002",
                test_files=(
                    "tests/test_defense_universe.py",
                    "tests/test_live_resolution.py",
                ),
            ),
            # Invariants (INV-001 through INV-020)
            RequirementTrace(
                req_id="INV-001",
                title="Add to Winners",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-006",
                test_files=("tests/test_pyramiding.py",),
            ),
            RequirementTrace(
                req_id="INV-002",
                title="Let Winners Run",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-007",
                test_files=("tests/test_continuation.py",),
            ),
            RequirementTrace(
                req_id="INV-003",
                title="Never Average Down",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-006",
                test_files=("tests/test_pyramiding.py",),
            ),
            RequirementTrace(
                req_id="INV-004",
                title="Never Add Risk to Losers",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-006",
                test_files=("tests/test_pyramiding.py",),
            ),
            RequirementTrace(
                req_id="INV-005",
                title="Cut Invalidations",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-004",
                test_files=("tests/test_entry_invalidation.py", "tests/test_exit_enforcement.py"),
            ),
            RequirementTrace(
                req_id="INV-006",
                title="Deterministic Execution Math",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-005",
                test_files=("tests/test_sizing.py", "tests/test_defense_sizing.py"),
            ),
            RequirementTrace(
                req_id="INV-007",
                title="One Execution Authority",
                category=RequirementCategory.INVARIANT,
                primary_story="GUARD-004",
                test_files=(
                    "tests/test_guardian_execution_gate.py",
                    "tests/test_paper_runner.py",
                    "tests/red_team/test_paper_runner_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="INV-008",
                title="UNKNOWN Is Not Safe",
                category=RequirementCategory.INVARIANT,
                primary_story="GUARD-006",
                test_files=(
                    "tests/test_guardian_emergency.py",
                    "tests/test_paper_kill_switch.py",
                    "tests/red_team/test_paper_kill_switch_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="INV-009",
                title="Broker Authority",
                category=RequirementCategory.INVARIANT,
                primary_story="BROKER-001",
                test_files=(
                    "tests/test_broker_account_reconciliation.py",
                    "tests/test_broker_position_reconciliation.py",
                ),
            ),
            RequirementTrace(
                req_id="INV-010",
                title="No Blind Retry",
                category=RequirementCategory.INVARIANT,
                primary_story="BROKER-004",
                test_files=(
                    "tests/test_broker_ambiguous_outcomes.py",
                    "tests/test_paper_kill_switch.py",
                ),
            ),
            RequirementTrace(
                req_id="INV-011",
                title="Protection Contract Enforcement",
                category=RequirementCategory.INVARIANT,
                primary_story="GUARD-005",
                test_files=("tests/test_guardian_protection_contract.py",),
            ),
            RequirementTrace(
                req_id="INV-012",
                title="Reserve Isolation",
                category=RequirementCategory.INVARIANT,
                primary_story="GUARD-003",
                test_files=("tests/test_guardian_capital.py",),
            ),
            RequirementTrace(
                req_id="INV-013",
                title="Core / Black Swan Independence",
                category=RequirementCategory.INVARIANT,
                primary_story="BS-002",
                test_files=(
                    "tests/test_defense_universe.py",
                    "tests/test_live_resolution.py",
                    "tests/red_team/test_live_resolution_adversarial.py",
                ),
            ),
            RequirementTrace(
                req_id="INV-014",
                title="No Autonomous Production Change",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-002",
                test_files=("tests/test_scientist_boundary.py", "tests/test_scientist_proposal.py"),
            ),
            RequirementTrace(
                req_id="INV-015",
                title="Human Approval",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-007",
                test_files=("tests/test_scientist_proposal.py", "tests/test_config.py"),
            ),
            RequirementTrace(
                req_id="INV-016",
                title="Append-Only Journal",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-001",
                test_files=("tests/test_scientist_journal.py", "tests/test_paper_journal.py"),
            ),
            RequirementTrace(
                req_id="INV-017",
                title="Reproducibility",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-005",
                test_files=("tests/test_scientist_backtest.py", "tests/test_scientist_oos.py"),
            ),
            RequirementTrace(
                req_id="INV-018",
                title="Good Loss / Bad Win",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-001",
                test_files=("tests/test_scientist_journal.py",),
            ),
            RequirementTrace(
                req_id="INV-019",
                title="Research, Not Reflex",
                category=RequirementCategory.INVARIANT,
                primary_story="SCI-002",
                test_files=("tests/test_scientist_boundary.py",),
            ),
            RequirementTrace(
                req_id="INV-020",
                title="Simplicity Invariant",
                category=RequirementCategory.INVARIANT,
                primary_story="TRADER-001",
                test_files=("tests/test_smoke.py", "tests/test_signals.py"),
            ),
        )

    @classmethod
    def audit_matrix(
        cls,
        repo_root: Path | None = None,
        audit_time: datetime | None = None,
    ) -> AcceptanceMatrixAuditReceipt:
        """Audit all requirements, verifying test file existence and traceability.

        Args:
            repo_root: Optional root directory of the repository.
            audit_time: Optional fixed timestamp for deterministic testing.

        Returns:
            AcceptanceMatrixAuditReceipt with digest and verified status.

        Raises:
            MissingTraceabilityError: If any requirement lacks test files or if file is missing.
            IncompleteAcceptanceMatrixError: If any requirement is not verified.
        """
        root = repo_root or Path(".")
        traces = cls.get_canonical_traces()
        effective_time = audit_time or datetime.now(UTC)

        for trace in traces:
            if not trace.test_files:
                raise MissingTraceabilityError(
                    f"Requirement {trace.req_id} ({trace.title}) has no test files declared."
                )

            for rel_path in trace.test_files:
                full_path = root / rel_path
                if not full_path.exists():
                    raise MissingTraceabilityError(
                        f"Test file '{rel_path}' for requirement {trace.req_id} does not exist."
                    )

        verified = [t for t in traces if t.status == "VERIFIED"]
        unverified = [t for t in traces if t.status != "VERIFIED"]

        if len(unverified) > 0:
            raise IncompleteAcceptanceMatrixError(
                f"Acceptance matrix is incomplete: {len(unverified)} requirements unverified."
            )

        # Generate deterministic cryptographic digest
        digest_payload = [
            {
                "id": t.req_id,
                "title": t.title,
                "story": t.primary_story,
                "status": t.status,
                "test_files": list(t.test_files),
            }
            for t in traces
        ]
        encoded = json.dumps(digest_payload, sort_keys=True).encode("utf-8")
        matrix_digest = hashlib.sha256(encoded).hexdigest()

        return AcceptanceMatrixAuditReceipt(
            timestamp=effective_time,
            total_requirements=len(traces),
            verified_count=len(verified),
            unverified_count=len(unverified),
            is_complete=True,
            trace_records=traces,
            matrix_digest=matrix_digest,
        )

    @classmethod
    def export_markdown_table(cls, receipt: AcceptanceMatrixAuditReceipt) -> str:
        """Format the acceptance matrix audit receipt as a Markdown table."""
        lines = [
            "# Acceptance Matrix Audit Report",
            f"- **Timestamp:** {receipt.timestamp.isoformat()}",
            f"- **Total Requirements:** {receipt.total_requirements}",
            f"- **Verified Requirements:** {receipt.verified_count} (100%)",
            f"- **Matrix Digest (SHA-256):** `{receipt.matrix_digest}`",
            "",
            "| Requirement | Title | Story | Verified Tests | Status |",
            "|---|---|---|---|:---:|",
        ]
        for trace in receipt.trace_records:
            tests_str = "<br>".join(trace.test_files)
            row = (
                f"| **{trace.req_id}** | {trace.title} | "
                f"{trace.primary_story} | {tests_str} | **{trace.status}** |"
            )
            lines.append(row)

        return "\n".join(lines)
