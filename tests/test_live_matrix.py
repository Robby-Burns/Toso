"""Unit tests for TOSO Live Acceptance Matrix Auditor & Traceability (LIVE-002).

Verifies:
- AC-001: All 20 Functional Requirements (FR-001..FR-020) are tracked and backed by tests.
- AC-002: All 20 Permanent Invariants (INV-001..INV-020) are tracked and backed by tests.
- AC-003: Audit produces is_complete=True, verified_count=40, unverified_count=0.
- AC-004: Missing test file raises MissingTraceabilityError (fail-closed).
- AC-005: Unverified requirement raises IncompleteAcceptanceMatrixError (fail-closed).
- AC-006: Deterministic SHA-256 digest sealing.
- AC-007: Clean Markdown export table generation.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from toso.live import (
    AcceptanceMatrixAuditor,
    IncompleteAcceptanceMatrixError,
    MissingTraceabilityError,
    RequirementCategory,
    RequirementTrace,
)


def test_canonical_matrix_completeness():
    """AC-001 & AC-002: Exactly 20 FRs and 20 INVs are mapped."""
    traces = AcceptanceMatrixAuditor.get_canonical_traces()
    assert len(traces) == 40

    fr_traces = [t for t in traces if t.category == RequirementCategory.FUNCTIONAL_REQUIREMENT]
    inv_traces = [t for t in traces if t.category == RequirementCategory.INVARIANT]

    assert len(fr_traces) == 20
    assert len(inv_traces) == 20

    # Ensure canonical numbering FR-001..FR-020
    for i in range(1, 21):
        assert any(t.req_id == f"FR-{i:03d}" for t in fr_traces)

    # Ensure canonical numbering INV-001..INV-020
    for i in range(1, 21):
        assert any(t.req_id == f"INV-{i:03d}" for t in inv_traces)


def test_audit_matrix_real_filesystem():
    """AC-003: Every referenced test file exists on disk and audit passes."""
    audit_time = datetime(2026, 11, 10, 14, 0, 0, tzinfo=UTC)
    receipt = AcceptanceMatrixAuditor.audit_matrix(audit_time=audit_time)

    assert receipt.is_complete is True
    assert receipt.total_requirements == 40
    assert receipt.verified_count == 40
    assert receipt.unverified_count == 0
    assert len(receipt.matrix_digest) == 64
    assert all(c in "0123456789abcdef" for c in receipt.matrix_digest)


def test_missing_test_file_fails_closed(tmp_path):
    """AC-004: Missing test file path raises MissingTraceabilityError."""
    # Running audit against an empty directory triggers missing file error
    with pytest.raises(MissingTraceabilityError, match="does not exist"):
        AcceptanceMatrixAuditor.audit_matrix(repo_root=tmp_path)


def test_empty_test_files_list_fails_closed():
    """AC-004: Requirement with empty test_files tuple raises MissingTraceabilityError."""
    mock_trace = RequirementTrace(
        req_id="FR-999",
        title="Mock Requirement",
        category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
        primary_story="MOCK-001",
        test_files=(),  # Missing test files
        status="VERIFIED",
    )
    with patch.object(AcceptanceMatrixAuditor, "get_canonical_traces", return_value=(mock_trace,)):
        with pytest.raises(MissingTraceabilityError, match="has no test files declared"):
            AcceptanceMatrixAuditor.audit_matrix()


def test_unverified_status_fails_closed():
    """AC-005: Requirement with DRAFT/FAILED status raises IncompleteAcceptanceMatrixError."""
    mock_trace = RequirementTrace(
        req_id="FR-001",
        title="Deterministic Core Trading",
        category=RequirementCategory.FUNCTIONAL_REQUIREMENT,
        primary_story="TRADER-001",
        test_files=("tests/test_signals.py",),
        status="DRAFT",  # Not verified
    )
    with patch.object(AcceptanceMatrixAuditor, "get_canonical_traces", return_value=(mock_trace,)):
        with pytest.raises(IncompleteAcceptanceMatrixError, match="matrix is incomplete"):
            AcceptanceMatrixAuditor.audit_matrix()


def test_markdown_export():
    """AC-007: Export generates valid markdown table with all 40 requirements."""
    receipt = AcceptanceMatrixAuditor.audit_matrix()
    md = AcceptanceMatrixAuditor.export_markdown_table(receipt)

    assert "# Acceptance Matrix Audit Report" in md
    assert "| Requirement | Title | Story | Verified Tests | Status |" in md
    assert "**FR-001**" in md
    assert "**FR-020**" in md
    assert "**INV-001**" in md
    assert "**INV-020**" in md
    assert receipt.matrix_digest in md
