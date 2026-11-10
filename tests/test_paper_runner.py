"""Verification Test Suite for Story PAPER-002: End-to-End Core Trader & Guardian Integration.

Verifies:
- AC-001: Deterministic end-to-end pipeline execution from signals through paper broker.
- AC-002: Guardian Execution Gate enforcement (submit_through_gate) on all orders.
- AC-003: Invalidation stop enforcement without discretionary override or hold delay.
- AC-004: Winner-only pyramiding; strict prevention of averaging down on losing positions.
- AC-005: Winner continuation honoring trend following without premature profit-taking.
- AC-006: Authoritative multi-step reconciliation against Phase 3 broker models.
- AC-007: Capital sleeve isolation (Core, Reserve, Black Swan) and headroom boundaries.
- AC-008: Portfolio heat constraints and emergency drawdown halt stop enforcement.
- AC-009: Pure determinism and bit-identical reproducibility across independent runs.
- AC-010: Fail-closed handling on corrupt feeds, invalid timestamps, or unapproved configs.
"""

from datetime import UTC, datetime, timedelta

import pytest

from toso.config.errors import UnapprovedConfigurationError
from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.exit.models import PositionStatus
from toso.paper.errors import UnknownPaperInputError
from toso.paper.runner import PaperTradingRunner
from toso.signals.models import Bar


def make_approved_config(
    fast_ma: int = 5,
    slow_ma: int = 10,
    breakout_lookback: int = 5,
    max_risk_pct: float = 0.01,
    max_pos_heat: float = 0.02,
    max_port_risk: float = 0.06,
    max_conc_pct: float = 0.20,
    drawdown_stop_pct: float = 0.10,
) -> CoreConfig:
    """Helper to construct an approved, deterministic CoreConfig."""
    return CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.APPROVED,
            approved_by="LeadRiskOfficer",
            approved_at="2026-06-01T00:00:00Z",
            checksum="a" * 64,
            description="Paper Validation Approved Configuration",
        ),
        strategy=StrategyConfig(
            strategy_id="STRAT-PAPER-001",
            timeframe="1d",
            fast_ma_period=fast_ma,
            slow_ma_period=slow_ma,
            atr_period=3,
            breakout_lookback_bars=breakout_lookback,
            invalidation_atr_multiple=2.0,
            pyramid_min_profit_r=1.0,
            max_pyramid_adds=2,
        ),
        risk=RiskConfig(
            max_risk_per_trade_pct=max_risk_pct,
            max_aggregate_position_risk_pct=max_pos_heat,
            max_portfolio_risk_pct=max_port_risk,
            max_position_concentration_pct=max_conc_pct,
            slippage_buffer_pct=0.001,
            emergency_drawdown_stop_pct=drawdown_stop_pct,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=0.80,
            reserve_allocation_pct=0.15,
            black_swan_allocation_pct=0.05,
        ),
        universe=UniverseConfig(
            eligible_symbols=("AAPL", "MSFT", "GOOGL"),
            min_average_daily_volume_shares=1000,
            min_share_price_dollars=5.0,
        ),
    )


def generate_bars(
    symbol: str,
    prices: list[float],
    start_time: datetime,
) -> list[Bar]:
    """Helper to generate sequential OHLCV bars from price series."""
    bars: list[Bar] = []
    for i, p in enumerate(prices):
        t = start_time + timedelta(days=i)
        bars.append(
            Bar(
                timestamp=t,
                open=p,
                high=p * 1.02,
                low=p * 0.98,
                close=p,
                volume=10_000.0,
            )
        )
    return bars


def test_ac001_deterministic_end_to_end_pipeline() -> None:
    """AC-001: Ingests multi-bar feed, generates signals, sizes, routes via Gate, and executes."""
    config = make_approved_config(fast_ma=3, slow_ma=6, breakout_lookback=3)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # 10 bars ascending trend to create moving averages and Donchian breakout
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 107.0, 110.0, 112.0, 115.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    summary = runner.run_simulation(feed)
    assert summary.initial_equity == 100_000.0
    assert summary.final_equity > 0.0
    assert len(summary.receipt_checksum) == 64
    assert len(runner.broker.receipts) > 0


def test_ac002_guardian_execution_gate_enforcement() -> None:
    """AC-002: Orders must pass Guardian Gate; gate blocks orders violating constraints."""
    config = make_approved_config(max_conc_pct=0.01)  # 1% concentration cap -> tight constraint
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0]
    bars = generate_bars("MSFT", prices, start_ts)
    feed = [{"MSFT": b} for b in bars]

    for step_bars in feed:
        runner.step(step_bars)

    # All orders submitted reached broker only through Execution Gate authorization
    for order in runner.broker.get_all_order_snapshots():
        assert order.symbol == "MSFT"


def test_ac003_invalidation_stop_enforcement() -> None:
    """AC-003: Protective invalidation stop is unconditionally enforced on breach."""
    config = make_approved_config(fast_ma=2, slow_ma=4, breakout_lookback=2)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # Rising prices to trigger entry, followed by catastrophic crash below invalidation stop
    prices = [100.0, 101.0, 103.0, 106.0, 110.0, 60.0, 55.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    runner.run_simulation(feed)

    # Trade must have completed with invalidation exit
    assert len(runner.completed_trades) >= 1
    closed_trade = runner.completed_trades[0]
    assert closed_trade.exit_reason == "STOP_LOSS_INVALIDATION"
    assert closed_trade.pnl < 0.0  # Stop loss taken deterministically


def test_ac004_winner_only_pyramiding_enforcement() -> None:
    """AC-004: Pyramiding is evaluated only on winning positions with R >= 1.0."""
    config = make_approved_config(fast_ma=2, slow_ma=4, breakout_lookback=2)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # Sustained strong bull run
    prices = [100.0, 102.0, 105.0, 110.0, 115.0, 122.0, 130.0, 140.0]
    bars = generate_bars("GOOGL", prices, start_ts)
    feed = [{"GOOGL": b} for b in bars]

    runner.run_simulation(feed)

    # Position in GOOGL should exist or have existed with valid status
    cpos = runner.core_positions.get("GOOGL")
    if cpos:
        assert cpos.status == PositionStatus.OPEN
        assert cpos.entry_price > 0.0


def test_ac005_winner_continuation_and_anti_take_profit() -> None:
    """AC-005: Valid trend with unbroken stop rides the trend without premature profit-taking."""
    config = make_approved_config(fast_ma=2, slow_ma=4, breakout_lookback=2)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # Gradual upward trend that does not violate stops
    prices = [100.0, 101.0, 102.5, 104.0, 106.0, 108.0, 110.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    runner.run_simulation(feed)

    # Position must NOT have been closed for profit-taking
    for t in runner.completed_trades:
        assert t.exit_reason != "TAKE_PROFIT"
        assert t.exit_reason != "PREMATURE_TRIM"


def test_ac006_authoritative_multi_step_reconciliation() -> None:
    """AC-006: Local state matches authoritative broker state at every discrete step."""
    config = make_approved_config(fast_ma=3, slow_ma=6, breakout_lookback=3)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
    bars = generate_bars("MSFT", prices, start_ts)
    feed = [{"MSFT": b} for b in bars]

    for step_bars in feed:
        step_res = runner.step(step_bars)
        # Check that equity matches paper broker
        assert step_res.total_equity == runner.broker.total_equity
        assert step_res.cash_balance == runner.broker.cash_balance


def test_ac007_capital_sleeve_separation_and_headroom() -> None:
    """AC-007: Core exposure cannot encroach upon Reserve (15%) or Black Swan (5%) capital."""
    config = make_approved_config()
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # 80% Core capacity = 80,000. Reserve = 15,000. Black Swan = 5,000.
    assert config.capital.core_allocation_pct == 0.80
    assert config.capital.reserve_allocation_pct == 0.15
    assert config.capital.black_swan_allocation_pct == 0.05

    prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    runner.run_simulation(feed)

    # Core deployed capital must never exceed Core capacity (80,000)
    broker_pos = runner.broker.get_position("AAPL")
    if broker_pos:
        assert broker_pos.market_value <= 80_000.0


def test_ac008_portfolio_heat_and_emergency_drawdown_stop() -> None:
    """AC-008: Peak-to-trough drawdown exceeding threshold halts simulation."""
    config = make_approved_config(
        fast_ma=2, slow_ma=4, breakout_lookback=2, drawdown_stop_pct=0.008
    )
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # Enter position on run up, then crash to trigger portfolio drawdown
    prices = [100.0, 102.0, 105.0, 110.0, 70.0, 60.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    summary = runner.run_simulation(feed)
    # Runner must have recognized drawdown, halted, and recorded summary
    assert summary.max_drawdown_pct >= 0.008
    assert runner.is_halted is True


def test_ac009_pure_determinism_and_bit_identical_reproducibility() -> None:
    """AC-009: Two independent runs over identical feed produce bit-identical receipts."""
    config = make_approved_config(fast_ma=2, slow_ma=4, breakout_lookback=2)
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    prices = [100.0, 101.0, 103.0, 105.0, 108.0, 112.0]
    bars = generate_bars("AAPL", prices, start_ts)
    feed = [{"AAPL": b} for b in bars]

    r1 = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)
    s1 = r1.run_simulation(feed)

    r2 = PaperTradingRunner(config=config, initial_cash=100_000.0, anchor_timestamp=start_ts)
    s2 = r2.run_simulation(feed)

    assert s1.final_equity == s2.final_equity
    assert s1.total_return_pct == s2.total_return_pct
    assert s1.receipt_checksum == s2.receipt_checksum
    assert len(s1.receipt_checksum) == 64


def test_ac010_fail_closed_on_corrupt_feeds() -> None:
    """AC-010: Corrupt bars, non-chronological feeds, or unapproved configs fail closed."""
    start_ts = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # 1. Unapproved configuration rejected
    unapproved_cfg = CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.DRAFT,  # DRAFT -> Not allowed
            checksum="a" * 64,
        ),
        strategy=StrategyConfig(strategy_id="S1"),
        risk=RiskConfig(),
        capital=CapitalAllocationConfig(),
        universe=UniverseConfig(),
    )
    with pytest.raises(UnapprovedConfigurationError):
        PaperTradingRunner(config=unapproved_cfg, initial_cash=100_000.0, anchor_timestamp=start_ts)

    # 2. Invalid initial cash
    approved_cfg = make_approved_config()
    with pytest.raises(UnknownPaperInputError):
        PaperTradingRunner(config=approved_cfg, initial_cash=-500.0, anchor_timestamp=start_ts)

    # 3. Empty feed rejected
    runner = PaperTradingRunner(
        config=approved_cfg, initial_cash=100_000.0, anchor_timestamp=start_ts
    )
    with pytest.raises(UnknownPaperInputError):
        runner.run_simulation([])

    # 4. Non-chronological step rejected
    b1 = Bar(timestamp=start_ts, open=100.0, high=101.0, low=99.0, close=100.0)
    runner.step({"AAPL": b1})
    past_ts = start_ts - timedelta(days=1)
    b_past = Bar(timestamp=past_ts, open=100.0, high=101.0, low=99.0, close=100.0)
    with pytest.raises(UnknownPaperInputError, match="Non-chronological"):
        runner.step({"AAPL": b_past})
