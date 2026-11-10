"""Tests for Story BS-003: Black Swan Defensive Sizing & Trigger Logic.

Verifies:
- FR-010: Capital Separation and Black Swan capacity limits.
- FR-011: Strict Core / Black Swan Independence.
- INV-013: Sleeve boundaries and sub-allocation caps.
- INV-008: Fail-closed gating on corrupted, NaN, or invalid inputs.
- INV-001, INV-006: Pure determinism and immutable audit receipts.
- Spec Section 12.1: Defensive trigger mechanisms, sub-allocation ceilings, and slippage buffers.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toso.defense import (
    BlackSwanCapitalSnapshot,
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveSizingExhaustedError,
    DefensiveSubAllocationCapExceededError,
    DefensiveTriggerConditionNotMetError,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    UnknownDefenseInputError,
    calculate_defensive_position_size,
    enforce_defensive_position_sizing,
    evaluate_defensive_trigger,
)


@pytest.fixture
def test_timestamp() -> datetime:
    return datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC)


@pytest.fixture
def capital_snapshot(test_timestamp: datetime) -> BlackSwanCapitalSnapshot:
    """Fixture providing a standard $1,000,000 equity portfolio with $50,000 defensive budget."""
    return BlackSwanCapitalSnapshot(
        portfolio_equity=1000000.0,
        allocation_pct=0.05,
        allocated_budget_dollars=50000.0,
        deployed_market_value_dollars=10000.0,
        available_capacity_dollars=40000.0,
        timestamp=test_timestamp,
    )


@pytest.fixture
def sh_spec() -> DefensiveInstrumentSpec:
    """ProShares Short S&P 500 (INVERSE_INDEX_ETF) with 50% max sleeve allocation."""
    return DefensiveInstrumentSpec(
        symbol="SH",
        instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
        underlying_index="S&P 500",
        leverage_factor=-1.0,
        max_allocation_of_sleeve_pct=0.50,
        max_holding_period_days=60,
        min_average_daily_volume=500000,
    )


@pytest.fixture
def spxu_spec() -> DefensiveInstrumentSpec:
    """ProShares UltraPro Short S&P 500 (LEVERAGED_INVERSE_ETF) with 25% max sleeve allocation."""
    return DefensiveInstrumentSpec(
        symbol="SPXU",
        instrument_type=DefensiveInstrumentType.LEVERAGED_INVERSE_ETF,
        underlying_index="S&P 500",
        leverage_factor=-3.0,
        max_allocation_of_sleeve_pct=0.25,
        max_holding_period_days=20,
        min_average_daily_volume=1000000,
    )


def test_ac001_trigger_evaluation_types(test_timestamp: datetime) -> None:
    """AC-001: Trigger evaluation handles volatility spikes, benchmark drops, and drawdowns."""
    # Volatility spike: VIX 35 >= threshold 30 -> TRIGGERED
    sig_vix = evaluate_defensive_trigger(
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=35.0,
        threshold_value=30.0,
        evaluation_timestamp=test_timestamp,
        severity_level=3,
    )
    assert sig_vix.is_triggered is True
    assert sig_vix.severity_level == 3
    assert sig_vix.trigger_type == DefensiveTriggerType.VOLATILITY_SPIKE

    # Volatility spike below threshold: VIX 22 < 30 -> NOT TRIGGERED
    sig_vix_quiet = evaluate_defensive_trigger(
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=22.0,
        threshold_value=30.0,
        evaluation_timestamp=test_timestamp,
    )
    assert sig_vix_quiet.is_triggered is False

    # Benchmark breakdown: return -6% <= threshold -5% -> TRIGGERED
    sig_bench = evaluate_defensive_trigger(
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.06,
        threshold_value=-0.05,
        evaluation_timestamp=test_timestamp,
        severity_level=4,
    )
    assert sig_bench.is_triggered is True

    # Portfolio drawdown: drawdown 8% >= threshold 7% -> TRIGGERED
    sig_dd = evaluate_defensive_trigger(
        trigger_type=DefensiveTriggerType.PORTFOLIO_DRAWDOWN_WARNING,
        observed_value=0.08,
        threshold_value=0.07,
        evaluation_timestamp=test_timestamp,
    )
    assert sig_dd.is_triggered is True


def test_ac002_inactive_trigger_allocates_zero_shares(
    sh_spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    test_timestamp: datetime,
) -> None:
    """AC-002: Inactive trigger allocates 0 shares; enforcement raises error."""
    inactive_signal = DefensiveTriggerSignal(
        signal_id="SIG-INACTIVE-01",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=False,
        observed_value=18.0,
        threshold_value=30.0,
        severity_level=1,
        timestamp=test_timestamp,
        rationale="Market calm",
    )

    receipt = calculate_defensive_position_size(
        spec=sh_spec,
        capital_snapshot=capital_snapshot,
        share_price=20.0,
        slippage_buffer_pct=0.005,
        trigger_signal=inactive_signal,
        evaluation_timestamp=test_timestamp,
    )
    assert receipt.shares == 0
    assert receipt.actual_cost_dollars == 0.0
    assert "not active" in receipt.rationale

    with pytest.raises(DefensiveTriggerConditionNotMetError):
        enforce_defensive_position_sizing(
            spec=sh_spec,
            capital_snapshot=capital_snapshot,
            share_price=20.0,
            slippage_buffer_pct=0.005,
            trigger_signal=inactive_signal,
            evaluation_timestamp=test_timestamp,
        )


def test_ac003_deterministic_sizing_bounded_by_sub_allocation_cap(
    spxu_spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    test_timestamp: datetime,
) -> None:
    """AC-003: Sizing is constrained by instrument sub-allocation ceiling (e.g. 25% = $12,500)."""
    # Total budget = $50,000. SPXU max_allocation_of_sleeve_pct = 0.25 -> Cap is $12,500.
    # Available capacity is $40,000, so spendable is min($40,000, $12,500) = $12,500.
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-01",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=True,
        observed_value=35.0,
        threshold_value=30.0,
        severity_level=3,
        timestamp=test_timestamp,
        rationale="Volatility breach",
    )

    # Share price $50.0, 0% slippage -> 12,500 // 50 = 250 shares ($12,500)
    receipt = calculate_defensive_position_size(
        spec=spxu_spec,
        capital_snapshot=capital_snapshot,
        share_price=50.0,
        slippage_buffer_pct=0.0,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )
    assert receipt.shares == 250
    assert receipt.actual_cost_dollars == 12500.0
    assert receipt.sub_allocation_cap_dollars == 12500.0
    assert receipt.remaining_capacity_dollars == 40000.0 - 12500.0

    enforced = enforce_defensive_position_sizing(
        spec=spxu_spec,
        capital_snapshot=capital_snapshot,
        share_price=50.0,
        slippage_buffer_pct=0.0,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )
    assert enforced.shares == 250


def test_ac004_slippage_buffer_dampens_position_size(
    spxu_spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    test_timestamp: datetime,
) -> None:
    """AC-004: Gap/slippage buffer increases effective share price, dampening share allocation."""
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-02",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        is_triggered=True,
        observed_value=-0.07,
        threshold_value=-0.05,
        severity_level=4,
        timestamp=test_timestamp,
        rationale="Market plunge",
    )

    # Spendable is $12,500. Share price is $50.0.
    # With 1% slippage buffer (0.01), effective price = $50.50.
    # 12,500 // 50.50 = 247 shares. Cost = 247 * 50.50 = $12,473.50.
    receipt = calculate_defensive_position_size(
        spec=spxu_spec,
        capital_snapshot=capital_snapshot,
        share_price=50.0,
        slippage_buffer_pct=0.01,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )
    assert receipt.shares == 247
    assert receipt.effective_share_price == 50.50
    assert receipt.actual_cost_dollars == 12473.50
    assert receipt.actual_cost_dollars <= receipt.sub_allocation_cap_dollars


def test_ac005_exhausted_capacity_blocked(
    sh_spec: DefensiveInstrumentSpec,
    test_timestamp: datetime,
) -> None:
    """AC-005: Zero or sub-share available capacity raises DefensiveSizingExhaustedError."""
    exhausted_snapshot = BlackSwanCapitalSnapshot(
        portfolio_equity=1000000.0,
        allocation_pct=0.05,
        allocated_budget_dollars=50000.0,
        deployed_market_value_dollars=49995.0,
        available_capacity_dollars=5.0,  # only $5 remaining
        timestamp=test_timestamp,
    )
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-03",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=True,
        observed_value=40.0,
        threshold_value=30.0,
        severity_level=5,
        timestamp=test_timestamp,
        rationale="Crisis",
    )

    # Share price $20.0 exceeds $5.0 capacity
    receipt = calculate_defensive_position_size(
        spec=sh_spec,
        capital_snapshot=exhausted_snapshot,
        share_price=20.0,
        slippage_buffer_pct=0.005,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )
    assert receipt.shares == 0

    with pytest.raises(DefensiveSizingExhaustedError) as exc_info:
        enforce_defensive_position_sizing(
            spec=sh_spec,
            capital_snapshot=exhausted_snapshot,
            share_price=20.0,
            slippage_buffer_pct=0.005,
            trigger_signal=active_signal,
            evaluation_timestamp=test_timestamp,
        )
    assert "exhausted" in str(exc_info.value)


def test_ac006_sub_allocation_cap_exceeded_blocked(
    test_timestamp: datetime,
) -> None:
    """AC-006: Sub-allocation cap smaller than 1 share raises cap error."""
    # Tiny cap spec: 0.0001 (0.01% of $50,000 = $5.00)
    tiny_spec = DefensiveInstrumentSpec(
        symbol="TINY",
        instrument_type=DefensiveInstrumentType.VOLATILITY_ETP,
        underlying_index="VIX",
        leverage_factor=1.0,
        max_allocation_of_sleeve_pct=0.0001,
        max_holding_period_days=10,
        min_average_daily_volume=100000,
    )
    snapshot = BlackSwanCapitalSnapshot(
        portfolio_equity=1000000.0,
        allocation_pct=0.05,
        allocated_budget_dollars=50000.0,
        deployed_market_value_dollars=0.0,
        available_capacity_dollars=50000.0,
        timestamp=test_timestamp,
    )
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-04",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=True,
        observed_value=40.0,
        threshold_value=30.0,
        severity_level=4,
        timestamp=test_timestamp,
        rationale="Crisis",
    )

    with pytest.raises(DefensiveSubAllocationCapExceededError):
        enforce_defensive_position_sizing(
            spec=tiny_spec,
            capital_snapshot=snapshot,
            share_price=25.0,  # $25 > $5 cap
            slippage_buffer_pct=0.005,
            trigger_signal=active_signal,
            evaluation_timestamp=test_timestamp,
        )


def test_fail_closed_on_corrupt_unknown_inputs(
    sh_spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    test_timestamp: datetime,
) -> None:
    """INV-008: Fail-closed on missing, NaN, infinite, or negative inputs."""
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-05",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=True,
        observed_value=40.0,
        threshold_value=30.0,
        severity_level=4,
        timestamp=test_timestamp,
        rationale="Crisis",
    )

    # Bad prices (0, negative, NaN, Inf)
    for bad_price in [0.0, -10.0, float("nan"), float("inf"), "100"]:
        with pytest.raises(UnknownDefenseInputError):
            calculate_defensive_position_size(
                spec=sh_spec,
                capital_snapshot=capital_snapshot,
                share_price=bad_price,  # type: ignore[arg-type]
                slippage_buffer_pct=0.005,
                trigger_signal=active_signal,
                evaluation_timestamp=test_timestamp,
            )

    # Bad slippage (negative, NaN, Inf)
    for bad_slip in [-0.01, float("nan"), float("inf"), "0.01"]:
        with pytest.raises(UnknownDefenseInputError):
            calculate_defensive_position_size(
                spec=sh_spec,
                capital_snapshot=capital_snapshot,
                share_price=20.0,
                slippage_buffer_pct=bad_slip,  # type: ignore[arg-type]
                trigger_signal=active_signal,
                evaluation_timestamp=test_timestamp,
            )

    # Bad trigger evaluation values
    for bad_val in [float("nan"), float("inf"), None, "spike"]:
        with pytest.raises(UnknownDefenseInputError):
            evaluate_defensive_trigger(
                trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
                observed_value=bad_val,  # type: ignore[arg-type]
                threshold_value=30.0,
                evaluation_timestamp=test_timestamp,
            )


def test_pure_determinism_and_immutable_receipts(
    sh_spec: DefensiveInstrumentSpec,
    capital_snapshot: BlackSwanCapitalSnapshot,
    test_timestamp: datetime,
) -> None:
    """INV-001, INV-006: Identical inputs produce identical receipts; models are frozen."""
    active_signal = DefensiveTriggerSignal(
        signal_id="SIG-ACTIVE-06",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        is_triggered=True,
        observed_value=40.0,
        threshold_value=30.0,
        severity_level=4,
        timestamp=test_timestamp,
        rationale="Crisis",
    )

    receipt1 = calculate_defensive_position_size(
        spec=sh_spec,
        capital_snapshot=capital_snapshot,
        share_price=20.0,
        slippage_buffer_pct=0.005,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )
    receipt2 = calculate_defensive_position_size(
        spec=sh_spec,
        capital_snapshot=capital_snapshot,
        share_price=20.0,
        slippage_buffer_pct=0.005,
        trigger_signal=active_signal,
        evaluation_timestamp=test_timestamp,
    )

    assert receipt1 == receipt2
    assert receipt1.timestamp == test_timestamp

    with pytest.raises(ValidationError):
        receipt1.shares = 999  # type: ignore[misc]

    with pytest.raises(ValidationError):
        active_signal.is_triggered = False  # type: ignore[misc]
