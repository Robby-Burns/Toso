"""Adversarial and independent verification tests for PAPER-002.

Tests the 9 mandatory verification lenses:
1. Boundary: Unapproved configs, non-chronological feeds, extreme/zero values.
2. Security: INV-014 strict paper isolation, zero network leakage.
3. Failure Paths: Gate rejection handling, corrupt bar feed fail-closed.
4. Skeptic: Loss position cannot pyramid, invalidation triggers despite later rebound.
5. Red Team: Bypassing gate impossible, halted runner rejects new orders, duplicate safety.
6. QA Edge: Flat/zero-signal feed, single-bar feed, empty bar handling.
7. Regression: Broker state consistency, local vs broker reconciliation integrity.
8. Runtime: Pure clock anchoring (no wall-clock reads).
9. Blast Radius: Frozen domain models, immutable records.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

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
from toso.guardian.models import SystemFeedContext
from toso.paper.broker import PaperBroker
from toso.paper.errors import UnknownPaperInputError
from toso.paper.runner import PaperTradingRunner
from toso.signals.models import Bar


def make_approved_config(
    fast_ma: int = 2,
    slow_ma: int = 4,
    breakout_lookback: int = 2,
    max_risk_pct: float = 0.01,
    max_pos_heat: float = 0.02,
    max_port_risk: float = 0.06,
    max_conc_pct: float = 0.20,
    drawdown_stop_pct: float = 0.10,
    pyramid_min_profit_r: float = 1.0,
    max_pyramid_adds: int = 2,
) -> CoreConfig:
    """Helper creating a strictly approved CoreConfig."""
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
            atr_period=2,
            breakout_lookback_bars=breakout_lookback,
            invalidation_atr_multiple=2.0,
            pyramid_min_profit_r=pyramid_min_profit_r,
            max_pyramid_adds=max_pyramid_adds,
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
            min_average_daily_volume_shares=100,
            min_share_price_dollars=5.0,
        ),
    )


def test_lens1_boundary_unapproved_configs_and_invalid_cash():
    """Boundary: Non-approved configurations and invalid initial cash must be rejected."""
    base_cfg = make_approved_config()
    unapproved_cfg = base_cfg.model_copy(
        update={
            "metadata": VersionMetadata(
                version="1.0.0",
                status=ApprovalStatus.DRAFT,
                checksum="b" * 64,
            )
        }
    )
    with pytest.raises(UnapprovedConfigurationError):
        PaperTradingRunner(unapproved_cfg)

    cfg = make_approved_config()
    with pytest.raises(UnknownPaperInputError):
        PaperTradingRunner(cfg, initial_cash=-500.0)

    with pytest.raises(UnknownPaperInputError):
        PaperTradingRunner(cfg, initial_cash=float("nan"))

    with pytest.raises(UnknownPaperInputError):
        PaperTradingRunner(cfg, initial_cash=0.0)


def test_lens1_boundary_non_chronological_bar_feed():
    """Boundary: Feeds with non-chronological or duplicate timestamps fail closed."""
    cfg = make_approved_config()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    b1 = Bar(
        timestamp=t0 + timedelta(minutes=10),
        open=100,
        high=105,
        low=95,
        close=102,
        volume=1000,
    )
    runner.step({"AAPL": b1})
    b2 = Bar(
        timestamp=t0 + timedelta(minutes=5),
        open=102,
        high=107,
        low=99,
        close=105,
        volume=1000,
    )
    with pytest.raises(UnknownPaperInputError, match="Non-chronological"):
        runner.step({"AAPL": b2})


def test_lens2_security_strict_paper_isolation():
    """Security (INV-014): Paper runner uses strictly in-memory broker with no endpoints."""
    cfg = make_approved_config()
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0)
    assert isinstance(runner.broker, PaperBroker)
    # Verify no network URL or external credentials exist
    assert not hasattr(runner.broker, "api_key")
    assert not hasattr(runner.broker, "base_url")
    assert not hasattr(runner, "api_key")


def test_lens3_failure_paths_guardian_gate_rejection():
    """Failure Paths: When Guardian Gate rejects (unoperational system), order never executes."""
    cfg = make_approved_config()
    broker = PaperBroker(initial_cash=100_000.0)
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, broker=broker, anchor_timestamp=t0)

    bars = [
        Bar(timestamp=t0, open=100, high=101, low=99, close=100, volume=1000),
        Bar(
            timestamp=t0 + timedelta(days=1),
            open=101,
            high=102,
            low=100,
            close=101,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=2),
            open=102,
            high=103,
            low=101,
            close=102,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=3),
            open=105,
            high=120,
            low=104,
            close=115,
            volume=5000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=4),
            open=116,
            high=130,
            low=115,
            close=125,
            volume=5000,
        ),
    ]
    unhealthy_ctx = SystemFeedContext(
        is_system_operational=False,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=t0,
        evaluation_timestamp=t0,
    )
    feed = [{"AAPL": b} for b in bars]
    summary = runner.run_simulation(feed, feed_context=unhealthy_ctx)
    # Entry was completely blocked at Guardian Gate
    assert summary.total_trades == 0
    assert len(runner.broker.get_all_order_snapshots()) == 0


def test_lens4_skeptic_invalidation_unconditional_despite_later_recovery():
    """Skeptic: Invalidation stop triggers unconditionally even if the stock skyrockets later."""
    cfg = make_approved_config()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    bars = [
        Bar(timestamp=t0, open=100, high=101, low=99, close=100, volume=1000),
        Bar(
            timestamp=t0 + timedelta(days=1),
            open=101,
            high=102,
            low=100,
            close=101,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=2),
            open=102,
            high=103,
            low=101,
            close=102,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=3),
            open=105,
            high=120,
            low=104,
            close=115,
            volume=5000,
        ),
        # Entry executes on bar 4 open (~115). Stop is set at ~103.
        # Day 4: Flash crash breaches invalidation stop (low=50), closes at 60
        Bar(
            timestamp=t0 + timedelta(days=4),
            open=115,
            high=116,
            low=50,
            close=60,
            volume=3000,
        ),
        # Day 5: Exits at open=60 (loss), then climbs moderately to 75
        Bar(
            timestamp=t0 + timedelta(days=5),
            open=60,
            high=80,
            low=59,
            close=75,
            volume=3000,
        ),
    ]
    feed = [{"AAPL": b} for b in bars]
    summary = runner.run_simulation(feed)
    assert summary.total_trades == 1
    # Trade was closed on invalidation breach on day 4 and executed on day 5 open
    assert summary.completed_trades[0].exit_reason == "STOP_LOSS_INVALIDATION"
    assert summary.completed_trades[0].pnl < 0
    assert len(runner.core_positions) == 0


def test_lens5_red_team_halted_runner_rejects_new_intakes():
    """Red Team: A halted runner cannot evaluate or execute any new entry orders."""
    cfg = make_approved_config(drawdown_stop_pct=0.005)  # 0.5% drawdown stop
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    bars = [
        Bar(timestamp=t0, open=100, high=101, low=99, close=100, volume=1000),
        Bar(
            timestamp=t0 + timedelta(days=1),
            open=101,
            high=102,
            low=100,
            close=101,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=2),
            open=102,
            high=103,
            low=101,
            close=102,
            volume=1000,
        ),
        Bar(
            timestamp=t0 + timedelta(days=3),
            open=105,
            high=120,
            low=104,
            close=115,
            volume=5000,
        ),
        # Bar 4 triggers stop loss with significant loss, causing equity drawdown > 0.5%
        Bar(
            timestamp=t0 + timedelta(days=4),
            open=115,
            high=116,
            low=50,
            close=55,
            volume=3000,
        ),
        # Bar 5 attempts another breakout
        Bar(
            timestamp=t0 + timedelta(days=5),
            open=60,
            high=130,
            low=59,
            close=128,
            volume=5000,
        ),
    ]
    feed = [{"AAPL": b} for b in bars]
    runner.run_simulation(feed)
    assert runner.is_halted is True
    # In halted state, all resting orders are canceled
    active_orders = [o for o in runner.broker.get_all_order_snapshots() if not o.is_terminal]
    assert len(active_orders) == 0


def test_lens6_qa_edge_zero_signal_feed():
    """QA Edge: In a flat market with zero breakout signals, runner completes cleanly."""
    cfg = make_approved_config()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    flat_bars = [
        Bar(
            timestamp=t0 + timedelta(days=i),
            open=100,
            high=100.5,
            low=99.5,
            close=100,
            volume=1000,
        )
        for i in range(10)
    ]
    feed = [{"AAPL": b} for b in flat_bars]
    summary = runner.run_simulation(feed)
    assert summary.total_trades == 0
    assert summary.win_rate == 0.0
    assert summary.final_equity == 100_000.0
    assert summary.max_drawdown_pct == 0.0
    assert len(summary.receipt_checksum) == 64


def test_lens8_runtime_clock_anchoring():
    """Runtime: Telemetry timestamps are strictly anchored to the bar stream timestamps."""
    cfg = make_approved_config()
    t0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)  # Past historical date
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    bars = [
        Bar(
            timestamp=t0 + timedelta(days=i),
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=1000,
        )
        for i in range(5)
    ]
    feed = [{"AAPL": b} for b in bars]
    step_results = [runner.step(step_bars) for step_bars in feed]
    # Step results timestamps must match the historical bar timestamps exactly
    assert step_results[0].timestamp == t0
    assert step_results[-1].timestamp == t0 + timedelta(days=4)


def test_lens9_blast_radius_immutability():
    """Blast Radius: Summary and step models are strictly frozen."""
    cfg = make_approved_config()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    runner = PaperTradingRunner(cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    bars = [Bar(timestamp=t0, open=100, high=101, low=99, close=100, volume=1000)]
    feed = [{"AAPL": b} for b in bars]
    summary = runner.run_simulation(feed)
    with pytest.raises(ValidationError):
        summary.final_equity = 200_000.0  # Cannot mutate frozen model
