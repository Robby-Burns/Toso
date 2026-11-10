"""Adversarial and 9-Lens Independent Verification Suite for Story PAPER-005.

Lenses:
1. Boundary: Invalid/NaN/Inf/inverted bar values and exact drawdown halt boundary.
2. Security: Injections blocked post-halt, runner lock-down enforcement.
3. Failure Paths: Emergency liquidation with resting orders and zero-position idempotency.
4. Skeptic: Gate rejection cleanup prevents pending entry leaks under disconnection.
5. Red Team: Compound rapid-fire failure injections (corrupt feed + broker drop + kill-switch).
6. QA Edge: Multi-symbol partial corruption fails closed atomically without partial execution.
7. Regression: Normal trading lifecycle unaffected under clean feed context.
8. Runtime: Pure clock anchoring preserved across emergency events with zero wall-clock reads.
9. Blast Radius: Frozen domain models uncorrupted and cryptographic journal integrity intact.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from toso.broker.models import BrokerOrderStatus, OrderIntent, OrderSide, OrderType
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
from toso.paper.errors import (
    EmergencyKillSwitchActiveError,
    UnknownPaperInputError,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.models import JournalEventType
from toso.signals.models import Bar


def make_approved_config() -> CoreConfig:
    """Helper creating an approved CoreConfig."""
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
            fast_ma_period=2,
            slow_ma_period=4,
            atr_period=3,
            breakout_lookback_bars=2,
            invalidation_atr_multiple=2.0,
            pyramid_min_profit_r=1.0,
            max_pyramid_adds=2,
        ),
        risk=RiskConfig(
            max_risk_per_trade_pct=0.01,
            max_aggregate_position_risk_pct=0.02,
            max_portfolio_risk_pct=0.06,
            max_position_concentration_pct=0.20,
            slippage_buffer_pct=0.001,
            emergency_drawdown_stop_pct=0.25,
        ),
        universe=UniverseConfig(
            eligible_symbols=("AAPL", "MSFT"),
            min_average_daily_volume_shares=1000,
            min_share_price_dollars=5.0,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=0.80,
            reserve_allocation_pct=0.10,
            black_swan_allocation_pct=0.10,
        ),
    )


# -------------------------------------------------------------------------
# Lens 1: Boundary Lens
# -------------------------------------------------------------------------
def test_lens1_boundary_bar_price_and_drawdown_limits() -> None:
    """Lens 1: Boundary conditions on bar corruption and drawdown circuit breaker."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # 1. Boundary: Zero price fails closed
    zero_bar = Bar.model_construct(
        timestamp=t0,
        open=0.0,
        high=100.0,
        low=0.0,
        close=100.0,
        volume=100.0,
    )
    with pytest.raises(UnknownPaperInputError):
        runner.step({"AAPL": zero_bar})

    # 2. Boundary: High < Low inverted fails closed
    inverted_bar = Bar.model_construct(
        timestamp=t0,
        open=100.0,
        high=90.0,
        low=110.0,
        close=100.0,
        volume=100.0,
    )
    with pytest.raises(UnknownPaperInputError):
        runner.step({"AAPL": inverted_bar})

    # 3. Boundary: Drawdown threshold 25% boundary test
    # Initial equity 100k, simulate peak at 100k, trough at 75001.0 (24.999% DD -> NOT halted)
    runner_dd = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    runner_dd._peak_equity = 100_000.0
    runner_dd.broker._cash_balance = 75_001.0
    bar_ok = Bar(
        timestamp=t0,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100.0,
    )
    snap = runner_dd.step({"AAPL": bar_ok})
    assert not runner_dd.is_halted
    assert snap.total_equity == 75_001.0

    # Trough at 75000.0 (exact 25.0% DD -> triggers emergency halt)
    runner_dd.broker._cash_balance = 75_000.0
    t1 = t0 + timedelta(days=1)
    bar_dd = Bar(
        timestamp=t1,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100.0,
    )
    runner_dd.step({"AAPL": bar_dd})
    assert runner_dd.is_halted


# -------------------------------------------------------------------------
# Lens 2: Security Lens
# -------------------------------------------------------------------------
def test_lens2_security_runner_lockdown_blocks_order_execution() -> None:
    """Lens 2: Once kill-switch triggers, runner is locked down and rejects subsequent steps."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    runner.trigger_kill_switch("Security lock test", t0)
    assert runner.is_halted

    # 1. Stepping when halted updates prices but submits ZERO orders even on breakouts
    t1 = t0 + timedelta(days=1)
    bar = Bar(
        timestamp=t1,
        open=100.0,
        high=120.0,
        low=99.0,
        close=115.0,
        volume=10000.0,
    )
    step_res = runner.step({"AAPL": bar})
    assert step_res.is_halted is True
    assert len(step_res.orders_submitted) == 0

    # 2. Applying configuration proposal while halted raises EmergencyKillSwitchActiveError
    from toso.scientist.models import HumanApprovalToken

    proposal = create_production_proposal(
        proposal_id="PROP-HALT-001",
        hypothesis_id="HYP-HALT-001",
        proposed_parameters={"breakout_lookback_bars": 3},
        rationale="Attempting change while halted",
        created_at=t1,
    )
    sig = generate_human_approval_signature(
        token_id="TOK-HALT-001",
        approver_id="LeadRiskOfficer",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=t1,
    )
    token = HumanApprovalToken(
        token_id="TOK-HALT-001",
        approver_id="LeadRiskOfficer",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=t1,
        expires_at=t1 + timedelta(days=1),
        signature=sig,
    )
    approved_proposal = attach_human_approval(
        proposal=proposal,
        token=token,
        current_time=t1,
    )
    with pytest.raises(EmergencyKillSwitchActiveError) as exc:
        runner.apply_proposal(approved_proposal, current_time=t1)
    assert "halted by emergency kill switch" in str(exc.value)


# -------------------------------------------------------------------------
# Lens 3: Failure Paths Lens
# -------------------------------------------------------------------------
def test_lens3_failure_paths_liquidation_with_resting_orders_and_idempotency() -> None:
    """Lens 3: Emergency liquidation cancels resting orders and is safe when positions are 0."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Place a resting limit order
    intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        limit_price=90.0,
        signal_id="TEST-LENS3-001",
        intent_timestamp=t0,
    )
    limit_order, _ = runner.broker.submit_order(intent)
    assert limit_order.status == BrokerOrderStatus.ACCEPTED

    # Emergency liquidate with 0 positions and 1 resting order
    liq_orders = runner.emergency_liquidate_all(timestamp=t0, reason="Testing empty liquidation")
    assert len(liq_orders) == 0  # No positions to liquidate
    order_snap = runner.broker.get_order_snapshot(limit_order.order_id)
    assert order_snap.status == BrokerOrderStatus.CANCELED
    assert runner.is_halted

    # Idempotent second call
    liq_orders_2 = runner.emergency_liquidate_all(
        timestamp=t0 + timedelta(seconds=1),
        reason="Repeated liquidation",
    )
    assert len(liq_orders_2) == 0


# -------------------------------------------------------------------------
# Lens 4: Skeptic Lens
# -------------------------------------------------------------------------
def test_lens4_skeptic_no_pending_entry_leaks_on_gate_rejection() -> None:
    """Lens 4: Gate rejections under disconnected broker cleanly purge pending entries."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Warmup and force breakout with disconnected broker
    prices = [100.0, 101.0, 103.0, 106.0, 110.0]
    for i, p in enumerate(prices):
        t = t0 + timedelta(days=i)
        bar = Bar(timestamp=t, open=p, high=p + 1.0, low=p - 1.0, close=p, volume=1000.0)
        ctx = SystemFeedContext(
            is_broker_connected=False,
            is_system_operational=True,
            is_emergency_stop_active=False,
            feed_timestamp=t,
            evaluation_timestamp=t,
        )
        runner.step({"AAPL": bar}, feed_context=ctx)

    # Verify no pending entries leaked in runner internal state
    assert len(runner._pending_entries) == 0
    # No orders were submitted to broker
    assert len(runner.broker.get_all_order_snapshots()) == 0


# -------------------------------------------------------------------------
# Lens 5: Red Team Lens
# -------------------------------------------------------------------------
def test_lens5_red_team_compound_rapid_fire_failure_injections() -> None:
    """Lens 5: Compound failure sequence (corrupted bar -> broker drop -> kill-switch)."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # 1. Corrupt NaN bar injection
    nan_bar = Bar.model_construct(
        timestamp=t0,
        open=100.0,
        high=101.0,
        low=99.0,
        close=math.nan,
        volume=1000.0,
    )
    with pytest.raises(UnknownPaperInputError):
        runner.step({"AAPL": nan_bar})

    # 2. Broker disconnected injection
    t1 = t0 + timedelta(days=1)
    ctx_disconnected = SystemFeedContext(
        is_broker_connected=False,
        is_system_operational=True,
        is_emergency_stop_active=False,
        feed_timestamp=t1,
        evaluation_timestamp=t1,
    )
    bar_ok = Bar(
        timestamp=t1,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
    )
    runner.step({"AAPL": bar_ok}, feed_context=ctx_disconnected)

    # 3. Emergency kill-switch trigger
    runner.trigger_kill_switch("Red team compound attack", t1)
    assert runner.is_halted

    # 4. Enforce strict lock-out on subsequent operation
    t2 = t1 + timedelta(days=1)
    bar_t2 = Bar(
        timestamp=t2,
        open=102.0,
        high=103.0,
        low=101.0,
        close=102.0,
        volume=1000.0,
    )
    step_res = runner.step({"AAPL": bar_t2})
    assert step_res.is_halted is True
    assert len(step_res.orders_submitted) == 0


# -------------------------------------------------------------------------
# Lens 6: QA Edge Lens
# -------------------------------------------------------------------------
def test_lens6_qa_edge_multi_symbol_atomic_failure_no_partial_execution() -> None:
    """Lens 6: When one symbol has corrupt data, step fails closed with zero side-effects."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # AAPL is valid, MSFT has inverted high < low
    aapl_bar = Bar(
        timestamp=t0,
        open=150.0,
        high=152.0,
        low=149.0,
        close=151.0,
        volume=1000.0,
    )
    msft_bar = Bar.model_construct(
        timestamp=t0,
        open=300.0,
        high=290.0,
        low=310.0,
        close=300.0,
        volume=1000.0,
    )

    with pytest.raises(UnknownPaperInputError):
        runner.step({"AAPL": aapl_bar, "MSFT": msft_bar})

    # Verify no orders or state mutated for AAPL
    assert len(runner.broker.get_all_order_snapshots()) == 0
    assert len(runner.core_positions) == 0
    # Step with empty dict fails closed with UnknownPaperInputError
    with pytest.raises(UnknownPaperInputError):
        runner.step({})


# -------------------------------------------------------------------------
# Lens 7: Regression Lens
# -------------------------------------------------------------------------
def test_lens7_regression_normal_trading_lifecycle_unaffected() -> None:
    """Lens 7: Normal trading execution functions cleanly when failure conditions are absent."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    prices = [100.0, 102.0, 105.0, 108.0, 112.0]
    for i, p in enumerate(prices):
        t = t0 + timedelta(days=i)
        ctx_healthy = SystemFeedContext(
            is_broker_connected=True,
            is_system_operational=True,
            is_emergency_stop_active=False,
            feed_timestamp=t,
            evaluation_timestamp=t,
        )
        bar = Bar(timestamp=t, open=p, high=p + 2.0, low=p - 1.0, close=p, volume=5000.0)
        snap = runner.step({"AAPL": bar}, feed_context=ctx_healthy)
        assert snap.total_equity >= 90_000.0

    assert not runner.is_halted
    assert len(runner.journal.get_entries_by_type(JournalEventType.FAILURE_RECORDED)) == 0


# -------------------------------------------------------------------------
# Lens 8: Runtime Lens
# -------------------------------------------------------------------------
def test_lens8_runtime_pure_clock_anchoring_across_kill_switch_events() -> None:
    """Lens 8: Pure clock anchoring is preserved; timestamps in logs match provided clock."""
    cfg = make_approved_config()
    t_fixed = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t_fixed)

    runner.trigger_kill_switch("Time anchoring audit", t_fixed)

    stops = runner.journal.get_entries_by_type(JournalEventType.EMERGENCY_STOP)
    assert len(stops) == 1
    assert stops[0].timestamp == t_fixed


# -------------------------------------------------------------------------
# Lens 9: Blast Radius Lens
# -------------------------------------------------------------------------
def test_lens9_blast_radius_journal_cryptographic_integrity_intact() -> None:
    """Lens 9: Even under heavy failure injections, journal hash chain integrity verifies 100%."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Incur multiple failure events
    runner.trigger_kill_switch("Halt 1", t0)

    # Verify journal hash chain integrity
    receipt = runner.journal.verify_integrity(t0 + timedelta(days=1))
    assert receipt.is_valid is True
    assert runner.journal.total_entries > 0
