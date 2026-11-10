"""Tests for Story TRADER-004: Invalidation Exit Enforcement.

Verifies:
- AC-001: Every Core position retains an active, deterministic invalidation definition.
- AC-002: Reaching or breaching invalidation triggers mandatory exit (intraday and gap).
- AC-003: Discretionary/Agent overrides, delays, or rewrites strictly fail closed.
- AC-004: Gap loss realization and strict anti-averaging-down invariants.
"""

from datetime import UTC, datetime, timedelta

import pytest

from toso.config.models import RiskConfig, StrategyConfig
from toso.entry.evaluator import create_entry_from_signal
from toso.entry.models import (
    CoreEntryOrder,
    GapSlippageRiskModel,
    InvalidationDefinition,
)
from toso.exit.errors import (
    AveragingDownProhibitedError,
    InvalidationExitOverrideError,
    InvalidPositionStateError,
)
from toso.exit.evaluator import (
    evaluate_position_exit,
    execute_invalidation_exit,
    reject_agent_exit_delay,
    verify_no_averaging_down,
)
from toso.exit.models import (
    CorePosition,
    ExitReason,
    PositionStatus,
)
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.models import Bar, BarSeries, SignalType


@pytest.fixture
def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="CORE-TREND-TEST",
        timeframe="1d",
        fast_ma_period=5,
        slow_ma_period=10,
        atr_period=5,
        breakout_lookback_bars=5,
        invalidation_atr_multiple=2.0,
        pyramid_min_profit_r=1.0,
        max_pyramid_adds=3,
    )


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.005,
        emergency_drawdown_stop_pct=0.10,
    )


@pytest.fixture
def sample_position() -> CorePosition:
    ts = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    invalidation = InvalidationDefinition(
        price=90.0,
        invalidation_type="ATR_STOP",
        atr_multiple=2.0,
        reference_atr=5.0,
        established_at=ts,
        is_ratcheted=False,
        reason="Deterministic 2.0x ATR stop",
    )
    risk_accounting = GapSlippageRiskModel.compute(
        entry_price=100.0, invalidation_price=90.0, slippage_buffer_pct=0.005
    )
    entry_order = CoreEntryOrder(
        order_id="ENTRY-AAPL-101",
        symbol="AAPL",
        timestamp=ts,
        side="BUY",
        entry_price=100.0,
        invalidation=invalidation,
        risk_accounting=risk_accounting,
        reason="Donchian breakout",
    )
    return CorePosition(
        position_id="POS-AAPL-001",
        symbol="AAPL",
        entry_order=entry_order,
        entry_price=100.0,
        quantity=100.0,
        invalidation=invalidation,
        status=PositionStatus.OPEN,
        opened_at=ts,
    )


# ============================================================================
# AC-001: Position Invalidation Integrity
# ============================================================================


def test_ac001_core_position_holds_immutable_invalidation(
    sample_position: CorePosition,
) -> None:
    """AC-001: Verifies position correctly records and preserves invalidation definition."""
    assert sample_position.status == PositionStatus.OPEN
    assert sample_position.invalidation.price == 90.0
    assert sample_position.entry_price == 100.0
    assert sample_position.quantity == 100.0
    assert sample_position.realized_pnl is None

    # Verify model immutability
    with pytest.raises(Exception):
        sample_position.quantity = 200.0  # type: ignore


# ============================================================================
# AC-002: Mandatory Invalidation Exit & Gap Handling
# ============================================================================


def test_ac002_intraday_breach_triggers_mandatory_exit_order(
    sample_position: CorePosition,
) -> None:
    """AC-002: Intraday price reaching stop triggers INVALIDATION_TRIGGERED order."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    # Market opens above stop (95.0), but trades down to 89.0 (breaching 90.0 stop)
    breach_bar = Bar(
        timestamp=bar_ts,
        open=95.0,
        high=96.0,
        low=89.0,
        close=91.0,
        volume=50000.0,
    )

    updated_pos, exit_order = evaluate_position_exit(sample_position, breach_bar)

    assert exit_order is not None
    assert exit_order.exit_reason == ExitReason.INVALIDATION_TRIGGERED
    assert exit_order.trigger_price == 90.0
    assert exit_order.invalidation_price == 90.0
    assert exit_order.quantity == 100.0
    assert exit_order.side == "SELL"
    assert updated_pos.status == PositionStatus.INVALIDATED
    assert updated_pos.exit_order == exit_order


def test_ac002_adverse_gap_down_triggers_gap_invalidation_order(
    sample_position: CorePosition,
) -> None:
    """AC-002: Overnight gap down opening below stop triggers GAP_INVALIDATION_TRIGGERED."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    # Market gaps down opening at 85.0 (below 90.0 stop)
    gap_bar = Bar(
        timestamp=bar_ts,
        open=85.0,
        high=87.0,
        low=84.0,
        close=86.0,
        volume=75000.0,
    )

    updated_pos, exit_order = evaluate_position_exit(sample_position, gap_bar)

    assert exit_order is not None
    assert exit_order.exit_reason == ExitReason.GAP_INVALIDATION_TRIGGERED
    assert exit_order.trigger_price == 85.0  # Triggered at open price, not phantom stop
    assert exit_order.invalidation_price == 90.0
    assert exit_order.quantity == 100.0
    assert updated_pos.status == PositionStatus.INVALIDATED


def test_ac002_no_breach_returns_none(
    sample_position: CorePosition,
) -> None:
    """AC-002: When price remains above stop, position remains OPEN without exit order."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    safe_bar = Bar(
        timestamp=bar_ts,
        open=98.0,
        high=103.0,
        low=95.0,  # Above 90.0 stop
        close=102.0,
        volume=30000.0,
    )

    updated_pos, exit_order = evaluate_position_exit(sample_position, safe_bar)

    assert exit_order is None
    assert updated_pos.status == PositionStatus.OPEN


def test_ac002_execute_invalidation_exit_closes_position_with_pnl(
    sample_position: CorePosition,
) -> None:
    """AC-002: Execution fill transitions position to CLOSED and records realized PnL."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    breach_bar = Bar(
        timestamp=bar_ts,
        open=95.0,
        high=96.0,
        low=89.0,
        close=91.0,
        volume=50000.0,
    )
    invalidated_pos, exit_order = evaluate_position_exit(sample_position, breach_bar)
    assert exit_order is not None

    fill_ts = bar_ts + timedelta(seconds=5)
    # Fill at stop price 90.0: PnL = (90 - 100) * 100 = -1000.0
    closed_pos = execute_invalidation_exit(
        position=invalidated_pos,
        exit_order=exit_order,
        fill_price=90.0,
        fill_timestamp=fill_ts,
    )

    assert closed_pos.status == PositionStatus.CLOSED
    assert closed_pos.closed_at == fill_ts
    assert closed_pos.realized_pnl == -1000.0


def test_ac002_idempotency_prevents_duplicate_close(
    sample_position: CorePosition,
) -> None:
    """AC-002 / Idempotency: Attempting to close an already-CLOSED position raises error."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    breach_bar = Bar(timestamp=bar_ts, open=95.0, high=96.0, low=89.0, close=91.0, volume=50000.0)
    invalidated_pos, exit_order = evaluate_position_exit(sample_position, breach_bar)
    assert exit_order is not None

    closed_pos = execute_invalidation_exit(invalidated_pos, exit_order, 90.0, bar_ts)

    # Attempt second close
    with pytest.raises(InvalidPositionStateError, match="is already CLOSED"):
        execute_invalidation_exit(closed_pos, exit_order, 90.0, bar_ts)

    # Attempt evaluating exit on closed position
    with pytest.raises(InvalidPositionStateError, match="is already CLOSED"):
        evaluate_position_exit(closed_pos, breach_bar)


# ============================================================================
# AC-003: Agent Boundary & No Discretionary Override
# ============================================================================


def test_ac003_agent_delay_request_strictly_rejected(
    sample_position: CorePosition,
) -> None:
    """AC-003: Agent request to delay invalidation exit raises InvalidationExitOverrideError."""
    with pytest.raises(
        InvalidationExitOverrideError, match="Discretionary and agent overrides .* prohibited"
    ):
        reject_agent_exit_delay(
            position=sample_position,
            agent_id="ResearchScientistAgent",
            request_type="DELAY_FOR_CONFIRMATION",
        )


def test_ac003_agent_cancel_request_strictly_rejected(
    sample_position: CorePosition,
) -> None:
    """AC-003: Agent request to cancel exit order raises InvalidationExitOverrideError."""
    with pytest.raises(
        InvalidationExitOverrideError, match="Discretionary and agent overrides .* prohibited"
    ):
        reject_agent_exit_delay(
            position=sample_position,
            agent_id="ResearchScientistAgent",
            request_type="CANCEL_EXIT_ORDER",
        )


def test_ac003_agent_thesis_rewrite_strictly_rejected(
    sample_position: CorePosition,
) -> None:
    """AC-003: Agent request to rewrite thesis raises InvalidationExitOverrideError."""
    with pytest.raises(
        InvalidationExitOverrideError, match="Discretionary and agent overrides .* prohibited"
    ):
        reject_agent_exit_delay(
            position=sample_position,
            agent_id="ResearchScientistAgent",
            request_type="REWRITE_THESIS",
        )


# ============================================================================
# AC-004: Adverse Gap Realization & Anti-Averaging-Down
# ============================================================================


def test_ac004_adverse_gap_execution_realizes_full_loss(
    sample_position: CorePosition,
) -> None:
    """AC-004: Adverse gap exit fills at gap price (80.0), realizing loss beyond stop distance."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    gap_bar = Bar(timestamp=bar_ts, open=80.0, high=82.0, low=79.0, close=81.0, volume=100000.0)

    invalidated_pos, exit_order = evaluate_position_exit(sample_position, gap_bar)
    assert exit_order is not None
    assert exit_order.trigger_price == 80.0

    # Fill occurs at gap open 80.0: Realized PnL = (80 - 100) * 100 = -2000.0
    # (exceeds nominal stop distance of -1000.0)
    closed_pos = execute_invalidation_exit(invalidated_pos, exit_order, 80.0, bar_ts)
    assert closed_pos.realized_pnl == -2000.0


def test_ac004_adding_to_declining_position_raises_averaging_down_error(
    sample_position: CorePosition,
) -> None:
    """AC-004: Adding exposure when price < entry_price is strictly prohibited."""
    # Entry was 100.0, current price is 95.0 (losing position)
    with pytest.raises(AveragingDownProhibitedError, match="Never Average Down"):
        verify_no_averaging_down(
            position=sample_position,
            additional_quantity=50.0,
            current_price=95.0,
        )


def test_ac004_adding_to_invalidated_position_raises_averaging_down_error(
    sample_position: CorePosition,
) -> None:
    """AC-004: Adding exposure to an INVALIDATED position is strictly prohibited."""
    bar_ts = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    breach_bar = Bar(timestamp=bar_ts, open=95.0, high=96.0, low=89.0, close=91.0, volume=50000.0)
    invalidated_pos, _ = evaluate_position_exit(sample_position, breach_bar)

    with pytest.raises(AveragingDownProhibitedError, match="is INVALIDATED"):
        verify_no_averaging_down(
            position=invalidated_pos,
            additional_quantity=50.0,
            current_price=91.0,
        )


def test_ac004_adding_to_winning_position_is_permitted(
    sample_position: CorePosition,
) -> None:
    """AC-004: Adding to a winning position (price > entry) passes verification."""
    # Entry was 100.0, current price is 115.0 (winning position)
    # Does not raise AveragingDownProhibitedError
    verify_no_averaging_down(
        position=sample_position,
        additional_quantity=50.0,
        current_price=115.0,
    )


# ============================================================================
# End-to-End Pipeline Integration Test
# ============================================================================


def test_end_to_end_entry_to_exit_lifecycle(
    strategy_config: StrategyConfig,
    risk_config: RiskConfig,
) -> None:
    """End-to-end: BarSeries -> Signal -> EntryOrder -> CorePosition -> Exit -> Closed."""
    # 1. Generate series resulting in BULLISH_ENTRY
    bars = []
    base_ts = 1700000000
    for i in range(15):
        price = 100.0 + (i * 2.0)
        bars.append(
            Bar(
                timestamp=datetime.fromtimestamp(base_ts + (i * 86400), tz=UTC),
                open=price - 0.5,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=10000.0,
            )
        )
    prev_high = max(b.high for b in bars[-6:-1])
    breakout_close = prev_high + 5.0
    bars[-1] = Bar(
        timestamp=bars[-1].timestamp,
        open=breakout_close - 1.0,
        high=breakout_close + 2.0,
        low=breakout_close - 2.0,
        close=breakout_close,
        volume=20000.0,
    )

    series = BarSeries(symbol="GOOGL", bars=tuple(bars))
    generator = DeterministicSignalGenerator(strategy_config)
    signal = generator.generate(series)
    assert signal.signal_type == SignalType.BULLISH_ENTRY

    # 2. Construct CoreEntryOrder
    entry_order = create_entry_from_signal(signal, strategy_config, risk_config)
    assert entry_order.invalidation.price == signal.invalidation_price

    # 3. Open CorePosition
    position = CorePosition(
        position_id="POS-GOOGL-001",
        symbol="GOOGL",
        entry_order=entry_order,
        entry_price=entry_order.entry_price,
        quantity=50.0,
        invalidation=entry_order.invalidation,
        status=PositionStatus.OPEN,
        opened_at=entry_order.timestamp,
    )
    assert position.status == PositionStatus.OPEN

    # 4. Severe adverse market event: price collapses through stop
    collapse_bar = Bar(
        timestamp=entry_order.timestamp + timedelta(days=1),
        open=position.invalidation.price - 5.0,  # Overnight gap down
        high=position.invalidation.price - 2.0,
        low=position.invalidation.price - 8.0,
        close=position.invalidation.price - 6.0,
        volume=500000.0,
    )

    # 5. Evaluate exit
    invalidated_pos, exit_order = evaluate_position_exit(position, collapse_bar)
    assert exit_order is not None
    assert exit_order.exit_reason == ExitReason.GAP_INVALIDATION_TRIGGERED
    assert invalidated_pos.status == PositionStatus.INVALIDATED

    # 6. Execute fill
    fill_time = collapse_bar.timestamp + timedelta(minutes=1)
    closed_pos = execute_invalidation_exit(
        position=invalidated_pos,
        exit_order=exit_order,
        fill_price=collapse_bar.open,
        fill_timestamp=fill_time,
    )
    assert closed_pos.status == PositionStatus.CLOSED
    assert closed_pos.realized_pnl is not None
    assert closed_pos.realized_pnl < 0.0
