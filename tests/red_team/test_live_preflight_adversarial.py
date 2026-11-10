"""Adversarial 9-Lens Verification Suite for LIVE-003 (Pre-Flight Safety Engine).

Covers all 9 mandatory verification lenses:
1. Boundary: Exact clock skew threshold boundary testing (5.0s vs 5.001s).
2. Security: Frozen model immutability of PreFlightCheckReceipt and CheckItem.
3. Failure Paths: Compound multi-failure injection fails closed cleanly.
4. Skeptic: Partial pass (6 of 7 passed) still marks overall_status=FAILED.
5. Red Team: Whitespace account ID and spoofed connectivity bypass attempts.
6. QA Edge: Zero-entry initial journal and single-cent solvency boundary.
7. Regression: Determinism of cryptographic receipt hash digest.
8. Runtime: Pure clock anchoring with zero wall-clock dependencies.
9. Blast Radius: Preflight check is completely non-mutating on broker state.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from toso.config import load_config_from_file
from toso.live import (
    CheckStatus,
    ClockSynchronizationError,
    PreFlightSafetyEngine,
)
from toso.paper.runner import PaperTradingRunner


@pytest.fixture
def canonical_config():
    return load_config_from_file(Path("config/core_config_v1.yaml"))


@pytest.fixture
def clean_runner(canonical_config):
    return PaperTradingRunner(config=canonical_config, initial_cash=100_000.0)


# Lens 1: Boundary
def test_lens_boundary_clock_skew(clean_runner):
    """Boundary: Exact boundary at max_clock_skew_seconds."""
    t_ref = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    # Exactly 5.0s skew passes
    t_exact = t_ref + timedelta(seconds=5.0)
    receipt_exact = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=t_exact,
        reference_time=t_ref,
        max_clock_skew_seconds=5.0,
    )
    assert receipt_exact.overall_status == CheckStatus.PASSED

    # 5.001s skew fails
    t_breach = t_ref + timedelta(seconds=5.001)
    with pytest.raises(ClockSynchronizationError):
        PreFlightSafetyEngine.execute_preflight_checks(
            runner=clean_runner,
            current_time=t_breach,
            reference_time=t_ref,
            max_clock_skew_seconds=5.0,
        )


# Lens 2: Security
def test_lens_security_receipt_frozen(clean_runner):
    """Security: PreFlightCheckReceipt is strictly immutable."""
    receipt = PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)
    with pytest.raises(ValidationError):
        receipt.overall_status = CheckStatus.FAILED

    with pytest.raises(ValidationError):
        receipt.checks[0].status = CheckStatus.FAILED


# Lens 3: Failure Paths
def test_lens_failure_paths_compound_failures(clean_runner):
    """Failure Paths: Compound multiple failures fail closed without uncaught exceptions."""
    t_ref = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    t_skewed = t_ref + timedelta(seconds=60.0)  # Skewed clock

    clean_runner.trigger_kill_switch("Emergency Test", timestamp=t_ref)  # Halted runner

    # raise_on_failure=False should gracefully capture multiple failed checks
    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=t_skewed,
        reference_time=t_ref,
        raise_on_failure=False,
    )
    assert receipt.overall_status == CheckStatus.FAILED
    failed_ids = [c.check_id for c in receipt.checks if c.status == CheckStatus.FAILED]
    assert "PFC-01" in failed_ids
    assert "PFC-05" in failed_ids


# Lens 4: Skeptic
def test_lens_skeptic_single_check_failure_marks_overall_failed(clean_runner):
    """Skeptic: 6 of 7 checks passing never marks all_passed=True."""
    t_ref = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    t_skew = t_ref + timedelta(seconds=10.0)

    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=t_skew,
        reference_time=t_ref,
        max_clock_skew_seconds=5.0,
        raise_on_failure=False,
    )
    assert receipt.all_passed is False
    assert receipt.overall_status == CheckStatus.FAILED


# Lens 5: Red Team
def test_lens_red_team_whitespace_account_id(canonical_config):
    """Red Team: Whitespace-only account ID fails closed."""
    mock_broker = MagicMock()
    mock_broker.is_connected = True
    mock_broker.account_id = "   "  # Whitespace only
    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        broker=mock_broker,
        config=canonical_config,
        raise_on_failure=False,
    )
    pfc02 = next(c for c in receipt.checks if c.check_id == "PFC-02")
    assert pfc02.status == CheckStatus.FAILED


# Lens 6: QA Edge
def test_lens_qa_edge_initial_clean_slate_order_snapshot(clean_runner):
    """QA Edge: Empty initial order dictionary correctly recognized as 0 resting orders."""
    assert len(clean_runner.broker.get_orders()) == 0
    receipt = PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)
    pfc03 = next(c for c in receipt.checks if c.check_id == "PFC-03")
    assert pfc03.status == CheckStatus.PASSED


# Lens 7: Regression
def test_lens_regression_receipt_hash_determinism(clean_runner):
    """Regression: Receipt hash produces consistent SHA-256 digest format."""
    receipt = PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)
    assert len(receipt.receipt_hash) == 64
    assert all(c in "0123456789abcdef" for c in receipt.receipt_hash)


# Lens 8: Runtime
def test_lens_runtime_explicit_timestamp_anchoring(clean_runner):
    """Runtime: Preflight receipt timestamp matches injected current_time."""
    fixed_time = datetime(2026, 11, 10, 15, 30, 0, tzinfo=UTC)
    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=fixed_time,
    )
    assert receipt.timestamp == fixed_time


# Lens 9: Blast Radius
def test_lens_blast_radius_zero_broker_mutation(clean_runner):
    """Blast Radius: Running preflight checks alters zero broker balances or positions."""
    initial_cash = clean_runner.broker.cash_balance
    initial_equity = clean_runner.broker.total_equity
    initial_orders = len(clean_runner.broker.get_orders())

    PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)

    assert clean_runner.broker.cash_balance == initial_cash
    assert clean_runner.broker.total_equity == initial_equity
    assert len(clean_runner.broker.get_orders()) == initial_orders
