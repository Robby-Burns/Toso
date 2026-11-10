"""Tests for TOSO Guardian Protection Contract & Recovery (GUARD-005).

Verifies:
- FR-014: Every open position requiring protective control shall have verified protection.
- AC-001: An open position without verified protection cannot receive additional exposure.
- AC-002: Deterministic recovery attempted when protection is missing, stale, or dropped.
- AC-003: Safety/liquidation response initiated when protection recovery fails.
- AC-004: Gap/slippage conditions demonstrate stop price is not a guaranteed cash maximum.
- INV-001, INV-006: Deterministic state evaluation, clock anchor echoing, immutable receipts.
- INV-008: Fail-closed handling for missing, non-positive, or NaN inputs.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from toso.guardian.errors import (
    InvalidProtectionStateError,
    UnverifiedProtectionError,
)
from toso.guardian.models import (
    ProtectionAction,
    ProtectionContract,
    ProtectionStatus,
)
from toso.guardian.protection import (
    calculate_gap_scenario_exposure,
    can_receive_additional_exposure,
    enforce_can_receive_additional_exposure,
    evaluate_protection_contract,
    execute_protection_recovery,
    verify_position_protection,
)


@pytest.fixture
def sample_ts() -> datetime:
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def test_ac001_unverified_protection_blocks_additional_exposure(sample_ts: datetime) -> None:
    """Lens: Boundary & Functional Gating (AC-001).

    An open position without verified protection cannot receive additional exposure.
    """
    # 1. Unprotected contract (missing stop order)
    unprotected_contract = ProtectionContract(
        position_id="POS-100",
        symbol="AAPL",
        shares=100,
        status=ProtectionStatus.UNPROTECTED,
        active_stop_order_id=None,
        active_stop_price=None,
        invalidation_price=90.0,
        is_verified=False,
        last_verified_at=sample_ts,
    )

    assert can_receive_additional_exposure(unprotected_contract) is False
    with pytest.raises(UnverifiedProtectionError, match="has unverified protection"):
        enforce_can_receive_additional_exposure(unprotected_contract)

    # 2. Verified contract permits additional exposure
    verified_contract = ProtectionContract(
        position_id="POS-100",
        symbol="AAPL",
        shares=100,
        status=ProtectionStatus.VERIFIED,
        active_stop_order_id="STOP-999",
        active_stop_price=90.0,
        invalidation_price=90.0,
        is_verified=True,
        last_verified_at=sample_ts,
    )

    assert can_receive_additional_exposure(verified_contract) is True
    # Does not raise
    enforce_can_receive_additional_exposure(verified_contract)


def test_ac002_deterministic_protection_recovery(sample_ts: datetime) -> None:
    """Lens: Failure Paths & Deterministic Recovery (AC-002).

    System attempts deterministic recovery when protection is missing or dropped.
    """
    contract = verify_position_protection(
        position_id="POS-200",
        symbol="MSFT",
        shares=50,
        invalidation_price=200.0,
        active_stop_order=None,  # Missing stop
        as_of_timestamp=sample_ts,
    )

    assert contract.status == ProtectionStatus.UNPROTECTED
    assert contract.is_verified is False

    decision = evaluate_protection_contract(contract, evaluation_timestamp=sample_ts)
    assert decision.action == ProtectionAction.ATTEMPT_RECOVERY
    assert decision.can_add_exposure is False

    # Execute recovery with mock submit_stop callback
    mock_submit_stop = Mock(return_value="STOP-NEW-123")
    mock_emergency_exit = Mock()

    updated_contract, action_result = execute_protection_recovery(
        contract=contract,
        submit_stop_fn=mock_submit_stop,
        emergency_exit_fn=mock_emergency_exit,
        evaluation_timestamp=sample_ts,
    )

    assert updated_contract.status == ProtectionStatus.VERIFIED
    assert updated_contract.is_verified is True
    assert updated_contract.active_stop_order_id == "STOP-NEW-123"
    assert updated_contract.recovery_attempts == 1
    mock_submit_stop.assert_called_once_with("POS-200", "MSFT", 50, 200.0)
    assert mock_emergency_exit.call_count == 0


def test_ac003_failed_recovery_triggers_safety_liquidation(sample_ts: datetime) -> None:
    """Lens: Security, Red Team & Failure Paths (AC-003).

    If protection cannot be restored, the approved safety/liquidation response is executed.
    """
    contract = ProtectionContract(
        position_id="POS-300",
        symbol="NVDA",
        shares=100,
        status=ProtectionStatus.UNPROTECTED,
        active_stop_order_id=None,
        active_stop_price=None,
        invalidation_price=120.0,
        recovery_attempts=1,  # 1 attempt already failed
        max_recovery_attempts=2,
        is_verified=False,
        last_verified_at=sample_ts,
    )

    # 1. Recovery attempt fails on the final permitted retry
    mock_failing_submit = Mock(side_effect=RuntimeError("Broker rejected stop order."))
    mock_emergency_exit = Mock(return_value={"status": "LIQUIDATION_SUBMITTED"})

    updated_contract, exit_res = execute_protection_recovery(
        contract=contract,
        submit_stop_fn=mock_failing_submit,
        emergency_exit_fn=mock_emergency_exit,
        evaluation_timestamp=sample_ts,
    )

    assert updated_contract.status == ProtectionStatus.LIQUIDATING
    assert updated_contract.is_verified is False
    assert exit_res == {"status": "LIQUIDATION_SUBMITTED"}
    mock_emergency_exit.assert_called_once_with("POS-300", "NVDA", 100)

    # 2. Re-evaluating contract in LIQUIDATING state mandates liquidation action
    decision = evaluate_protection_contract(updated_contract, evaluation_timestamp=sample_ts)
    assert decision.action == ProtectionAction.TRIGGER_SAFETY_LIQUIDATION
    assert decision.can_add_exposure is False


def test_ac004_gap_scenario_demonstrates_non_guaranteed_stop_price(
    sample_ts: datetime,
) -> None:
    """Lens: Adverse Gap Stress & Risk Reality (AC-004, FR-014).

    Protection does not guarantee a fill at the protected price during adverse gap conditions.
    """
    contract = ProtectionContract(
        position_id="POS-400",
        symbol="TSLA",
        shares=100,
        status=ProtectionStatus.VERIFIED,
        active_stop_order_id="STOP-TSLA",
        active_stop_price=190.0,
        invalidation_price=190.0,
        is_verified=True,
        last_verified_at=sample_ts,
    )

    entry_price = 200.0
    nominal_risk = (entry_price - 190.0) * 100  # $1,000

    # 1. Normal exit at stop loss
    normal_loss = calculate_gap_scenario_exposure(contract, entry_price, gap_down_pct=0.05)
    assert normal_loss == nominal_risk

    # 2. Adverse overnight gap down of 20% (opening at $160)
    adverse_gap_loss = calculate_gap_scenario_exposure(contract, entry_price, gap_down_pct=0.20)
    expected_gap_loss = (200.0 * 0.20) * 100  # $4,000
    assert adverse_gap_loss == expected_gap_loss
    assert adverse_gap_loss > nominal_risk


def test_stale_and_looser_protection_rejected(sample_ts: datetime) -> None:
    """Lens: Boundary & Skeptic.

    Stale verification timestamps or stops looser than required invalidation fail verification.
    """
    # 1. Stop looser than required invalidation ($85 vs required $90)
    looser_stop = {"order_id": "STOP-BAD", "stop_price": 85.0, "shares": 100}
    contract = verify_position_protection(
        position_id="POS-500",
        symbol="AAPL",
        shares=100,
        invalidation_price=90.0,
        active_stop_order=looser_stop,
        as_of_timestamp=sample_ts,
    )
    assert contract.status == ProtectionStatus.UNPROTECTED
    assert contract.is_verified is False

    # 2. Verification older than max_staleness_seconds
    verified_contract = ProtectionContract(
        position_id="POS-501",
        symbol="AAPL",
        shares=100,
        status=ProtectionStatus.VERIFIED,
        active_stop_order_id="STOP-GOOD",
        active_stop_price=90.0,
        invalidation_price=90.0,
        is_verified=True,
        last_verified_at=sample_ts - timedelta(seconds=120),  # Stale > 60s
    )
    decision = evaluate_protection_contract(
        verified_contract, evaluation_timestamp=sample_ts, max_staleness_seconds=60.0
    )
    assert decision.action == ProtectionAction.ATTEMPT_RECOVERY
    assert decision.can_add_exposure is False
    assert "stale" in decision.reason


def test_fail_closed_invalid_inputs(sample_ts: datetime) -> None:
    """Lens: QA Edge & Fail-Closed (INV-008)."""
    with pytest.raises(InvalidProtectionStateError, match="position_id"):
        verify_position_protection(
            position_id="",
            symbol="AAPL",
            shares=100,
            invalidation_price=90.0,
            active_stop_order=None,
            as_of_timestamp=sample_ts,
        )

    with pytest.raises(InvalidProtectionStateError, match="shares"):
        verify_position_protection(
            position_id="POS-1",
            symbol="AAPL",
            shares=-5,
            invalidation_price=90.0,
            active_stop_order=None,
            as_of_timestamp=sample_ts,
        )

    with pytest.raises(InvalidProtectionStateError, match="invalidation_price"):
        verify_position_protection(
            position_id="POS-1",
            symbol="AAPL",
            shares=100,
            invalidation_price=float("nan"),
            active_stop_order=None,
            as_of_timestamp=sample_ts,
        )


def test_determinism_and_immutability(sample_ts: datetime) -> None:
    """Lens: Skeptic & Runtime (INV-001, INV-006)."""
    contract = ProtectionContract(
        position_id="POS-700",
        symbol="GOOGL",
        shares=50,
        status=ProtectionStatus.VERIFIED,
        active_stop_order_id="STOP-700",
        active_stop_price=150.0,
        invalidation_price=150.0,
        is_verified=True,
        last_verified_at=sample_ts,
    )

    dec1 = evaluate_protection_contract(contract, evaluation_timestamp=sample_ts)
    dec2 = evaluate_protection_contract(contract, evaluation_timestamp=sample_ts)

    assert dec1 == dec2
    assert dec1.model_dump_json() == dec2.model_dump_json()
    assert dec1.timestamp == sample_ts

    with pytest.raises(ValidationError):
        contract.is_verified = False  # type: ignore[misc]
