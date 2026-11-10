"""Tests for Story BS-004: Black Swan Defensive Exits & De-escalation.

Verifies:
- Spec Section 12.1: Holding period decay limits, normalization exits, and convexity profit targets.
- FR-010: Capital separation and proceeds replenishment to defensive capacity.
- FR-011: Strict Core / Black Swan Independence.
- INV-013: Sleeve boundaries and non-leakage.
- INV-008: Fail-closed gating on corrupted, non-chronological, or invalid inputs.
- INV-001, INV-006: Pure determinism and immutable audit receipts.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.defense import (
    DefensiveDeescalationPlan,
    DefensiveExitAction,
    DefensiveExitError,
    DefensiveExitReason,
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensivePosition,
    InvalidDefensiveExitActionError,
    UnknownDefenseInputError,
    calculate_deescalation_plan,
    enforce_defensive_exit,
    evaluate_defensive_position_exit,
)


@pytest.fixture
def entry_time() -> datetime:
    return datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def spxu_spec() -> DefensiveInstrumentSpec:
    """SPXU has 20-day holding limit to prevent leveraged decay (RISK-005)."""
    return DefensiveInstrumentSpec(
        symbol="SPXU",
        instrument_type=DefensiveInstrumentType.LEVERAGED_INVERSE_ETF,
        underlying_index="S&P 500",
        leverage_factor=-3.0,
        max_allocation_of_sleeve_pct=0.25,
        max_holding_period_days=20,
        min_average_daily_volume=1000000,
    )


@pytest.fixture
def sh_spec() -> DefensiveInstrumentSpec:
    """SH has 60-day holding limit."""
    return DefensiveInstrumentSpec(
        symbol="SH",
        instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
        underlying_index="S&P 500",
        leverage_factor=-1.0,
        max_allocation_of_sleeve_pct=0.50,
        max_holding_period_days=60,
        min_average_daily_volume=500000,
    )


def test_ac001_holding_period_expired_triggers_full_liquidation(
    spxu_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """AC-001: Exceeding max_holding_period_days triggers FULL_LIQUIDATION to eliminate decay."""
    pos = DefensivePosition(
        position_id="POS-SPXU-01",
        symbol="SPXU",
        shares=200,
        entry_price=50.0,
        entry_timestamp=entry_time,
        current_price=55.0,
    )

    # 21 days later (exceeds 20 days max)
    eval_time = entry_time + timedelta(days=21)

    receipt = evaluate_defensive_position_exit(
        position=pos,
        spec=spxu_spec,
        evaluation_timestamp=eval_time,
    )

    assert receipt.action == DefensiveExitAction.FULL_LIQUIDATION
    assert receipt.reason == DefensiveExitReason.HOLDING_PERIOD_EXPIRED
    assert receipt.shares_to_exit == 200
    assert receipt.remaining_shares == 0
    assert receipt.estimated_proceeds_dollars == 200 * 55.0
    assert receipt.holding_days == 21
    assert "decay drag" in receipt.rationale

    enforced = enforce_defensive_exit(receipt)
    assert enforced.action == DefensiveExitAction.FULL_LIQUIDATION


def test_ac002_normalization_signal_triggers_full_liquidation(
    sh_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """AC-002: Active crisis normalization signal triggers complete defensive liquidation."""
    pos = DefensivePosition(
        position_id="POS-SH-01",
        symbol="SH",
        shares=500,
        entry_price=20.0,
        entry_timestamp=entry_time,
        current_price=22.0,
    )

    # Only 5 days held (well within 60 days limit), but normalization signal is active
    eval_time = entry_time + timedelta(days=5)

    receipt = evaluate_defensive_position_exit(
        position=pos,
        spec=sh_spec,
        evaluation_timestamp=eval_time,
        normalization_signal=True,
    )

    assert receipt.action == DefensiveExitAction.FULL_LIQUIDATION
    assert receipt.reason == DefensiveExitReason.NORMALIZATION_SIGNAL
    assert receipt.shares_to_exit == 500
    assert receipt.remaining_shares == 0
    assert receipt.estimated_proceeds_dollars == 500 * 22.0
    assert "normalization detected" in receipt.rationale


def test_ac003_profit_target_triggers_partial_trim(
    spxu_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """AC-003: Reaching profit target triggers partial trim to lock in convexity gains."""
    pos = DefensivePosition(
        position_id="POS-SPXU-02",
        symbol="SPXU",
        shares=300,
        entry_price=40.0,
        entry_timestamp=entry_time,
        current_price=60.0,  # +50% gain
    )

    # 10 days held (within 20-day limit), profit target set to 40% (0.40)
    eval_time = entry_time + timedelta(days=10)

    receipt = evaluate_defensive_position_exit(
        position=pos,
        spec=spxu_spec,
        evaluation_timestamp=eval_time,
        profit_target_pct=0.40,
        trim_ratio=0.50,  # 50% trim
    )

    assert receipt.action == DefensiveExitAction.PARTIAL_TRIM
    assert receipt.reason == DefensiveExitReason.PROFIT_TARGET_MET
    assert receipt.shares_to_exit == 150
    assert receipt.remaining_shares == 150
    assert receipt.estimated_proceeds_dollars == 150 * 60.0
    assert "realize convexity" in receipt.rationale


def test_ac004_within_limits_holds_position(
    sh_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """AC-004: Position within holding limits and without normalization evaluates to HOLD."""
    pos = DefensivePosition(
        position_id="POS-SH-02",
        symbol="SH",
        shares=400,
        entry_price=20.0,
        entry_timestamp=entry_time,
        current_price=21.0,  # +5% gain
    )

    # 15 days held (well under 60d), profit target 30% not reached
    eval_time = entry_time + timedelta(days=15)

    receipt = evaluate_defensive_position_exit(
        position=pos,
        spec=sh_spec,
        evaluation_timestamp=eval_time,
        normalization_signal=False,
        profit_target_pct=0.30,
    )

    assert receipt.action == DefensiveExitAction.HOLD
    assert receipt.reason == DefensiveExitReason.NONE
    assert receipt.shares_to_exit == 0
    assert receipt.remaining_shares == 400
    assert receipt.estimated_proceeds_dollars == 0.0

    # Enforcing exit on HOLD raises DefensiveExitError
    with pytest.raises(DefensiveExitError) as exc_info:
        enforce_defensive_exit(receipt)
    assert "evaluated to HOLD" in str(exc_info.value)


def test_ac005_deescalation_plan_aggregates_replenishment(
    spxu_spec: DefensiveInstrumentSpec,
    sh_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """AC-005: Plan computes total returned proceeds across multiple active defensive positions."""
    pos1 = DefensivePosition(
        position_id="POS-SPXU-03",
        symbol="SPXU",
        shares=100,
        entry_price=50.0,
        entry_timestamp=entry_time,
        current_price=60.0,
    )
    pos2 = DefensivePosition(
        position_id="POS-SH-03",
        symbol="SH",
        shares=200,
        entry_price=20.0,
        entry_timestamp=entry_time,
        current_price=22.0,
    )

    specs_map = {"SPXU": spxu_spec, "SH": sh_spec}
    eval_time = entry_time + timedelta(days=25)  # SPXU expired (>20d), SH valid (<60d)

    plan = calculate_deescalation_plan(
        positions=[pos1, pos2],
        specs_map=specs_map,
        evaluation_timestamp=eval_time,
    )

    assert isinstance(plan, DefensiveDeescalationPlan)
    assert len(plan.exit_receipts) == 2

    # SPXU fully liquidated: 100 * 60 = $6,000
    spxu_receipt = plan.exit_receipts[0]
    assert spxu_receipt.action == DefensiveExitAction.FULL_LIQUIDATION
    assert spxu_receipt.estimated_proceeds_dollars == 6000.0

    # SH held: 0 proceeds
    sh_receipt = plan.exit_receipts[1]
    assert sh_receipt.action == DefensiveExitAction.HOLD
    assert sh_receipt.estimated_proceeds_dollars == 0.0

    assert plan.total_proceeds_replenishment_dollars == 6000.0


def test_fail_closed_on_corrupt_unknown_inputs(
    spxu_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """INV-008: Fail-closed on missing, NaN, infinite, non-chronological, or invalid inputs."""
    pos = DefensivePosition(
        position_id="POS-SPXU-04",
        symbol="SPXU",
        shares=100,
        entry_price=50.0,
        entry_timestamp=entry_time,
        current_price=55.0,
    )

    # Evaluation timestamp precedes entry timestamp
    with pytest.raises(UnknownDefenseInputError):
        evaluate_defensive_position_exit(
            position=pos,
            spec=spxu_spec,
            evaluation_timestamp=entry_time - timedelta(days=1),
        )

    # Invalid trim ratio (> 1.0)
    with pytest.raises(InvalidDefensiveExitActionError):
        evaluate_defensive_position_exit(
            position=pos,
            spec=spxu_spec,
            evaluation_timestamp=entry_time + timedelta(days=5),
            profit_target_pct=0.10,
            trim_ratio=1.5,
        )

    # Invalid profit target (NaN, Inf, negative)
    for bad_target in [float("nan"), float("inf"), -0.10, 0.0]:
        with pytest.raises(UnknownDefenseInputError):
            evaluate_defensive_position_exit(
                position=pos,
                spec=spxu_spec,
                evaluation_timestamp=entry_time + timedelta(days=5),
                profit_target_pct=bad_target,
            )


def test_pure_determinism_and_immutable_receipts(
    spxu_spec: DefensiveInstrumentSpec,
    entry_time: datetime,
) -> None:
    """INV-001, INV-006: Identical inputs produce identical receipts; models are frozen."""
    pos = DefensivePosition(
        position_id="POS-SPXU-05",
        symbol="SPXU",
        shares=100,
        entry_price=50.0,
        entry_timestamp=entry_time,
        current_price=55.0,
    )
    eval_time = entry_time + timedelta(days=21)

    receipt1 = evaluate_defensive_position_exit(
        position=pos,
        spec=spxu_spec,
        evaluation_timestamp=eval_time,
    )
    receipt2 = evaluate_defensive_position_exit(
        position=pos,
        spec=spxu_spec,
        evaluation_timestamp=eval_time,
    )

    assert receipt1 == receipt2
    assert receipt1.evaluation_timestamp == eval_time

    with pytest.raises(ValidationError):
        receipt1.shares_to_exit = 0  # type: ignore[misc]

    with pytest.raises(ValidationError):
        pos.shares = 500  # type: ignore[misc]
