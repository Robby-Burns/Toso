"""TOSO Paper Validation - Story PAPER-003 Verification Test Suite.

Verifies Black Swan Defense integration in the simulated paper environment:
- AC-001 / Defensive Capital Sleeve Isolation (INV-004, INV-013)
- AC-002 / Non-Overlapping Instrument Universe (FR-010, FR-011)
- AC-003 / Deterministic Trigger Ingestion & Sizing (FR-010, INV-001)
- AC-004 / Guardian Execution Gate Enforcement for Defense
- AC-005 / Defensive Execution on PaperBroker
- AC-006 / De-escalation & Orderly Exit (FR-011)
- AC-007 / Authoritative Multi-Sleeve Reconciliation
- AC-008 / Co-existence under Market Stress
- AC-009 / Determinism & Cryptographic Audit (INV-001, INV-006)
- AC-010 / Fail-Closed on Corrupt or Disallowed Defensive Inputs (INV-008, INV-014)
"""

from datetime import UTC, datetime, timedelta

import pytest

from toso.broker.positions import reconcile_positions
from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.defense.capital import evaluate_black_swan_capital_intake
from toso.defense.errors import (
    BlackSwanSleeveBreachError,
    CoreDefensiveUniverseCollisionError,
    UnknownDefenseInputError,
)
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    DefensiveUniverseConfig,
)
from toso.defense.universe import load_canonical_defensive_universe
from toso.guardian.models import CapitalSleeve
from toso.paper.runner import PaperTradingRunner
from toso.signals.models import Bar


def make_approved_config(
    core_alloc: float = 0.80,
    reserve_alloc: float = 0.10,
    black_swan_alloc: float = 0.10,
    fast_ma: int = 2,
    slow_ma: int = 4,
    breakout_lookback: int = 2,
) -> CoreConfig:
    """Helper creating an approved CoreConfig with partitioned sleeves."""
    return CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.APPROVED,
            approved_by="LeadRiskOfficer",
            approved_at="2026-06-01T00:00:00Z",
            checksum="c" * 64,
            description="Paper Defense Approved Configuration",
        ),
        strategy=StrategyConfig(
            strategy_id="STRAT-DEF-001",
            timeframe="1d",
            fast_ma_period=fast_ma,
            slow_ma_period=slow_ma,
            atr_period=2,
            breakout_lookback_bars=breakout_lookback,
            invalidation_atr_multiple=2.0,
        ),
        risk=RiskConfig(
            max_risk_per_trade_pct=0.01,
            max_aggregate_position_risk_pct=0.02,
            max_portfolio_risk_pct=0.06,
            max_position_concentration_pct=0.20,
            slippage_buffer_pct=0.001,
            emergency_drawdown_stop_pct=0.15,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=core_alloc,
            reserve_allocation_pct=reserve_alloc,
            black_swan_allocation_pct=black_swan_alloc,
        ),
        universe=UniverseConfig(
            eligible_symbols=("AAPL", "MSFT"),
            min_average_daily_volume_shares=100,
            min_share_price_dollars=5.0,
        ),
    )


def test_ac001_defensive_capital_sleeve_isolation() -> None:
    """AC-001: Black Swan sleeve exposure cannot exceed configured allocation pct."""
    cfg = make_approved_config(core_alloc=0.80, reserve_alloc=0.10, black_swan_alloc=0.10)
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    # 10% black swan on $100k = $10,000 capacity. Max allocation for VIXY is 50% = $5,000.
    assert runner.config.capital.black_swan_allocation_pct == 0.10

    # Step with a defensive trigger
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-001",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=45.0,
        threshold_value=30.0,
        is_triggered=True,
        severity_level=2,
        rationale="VIX spike > 30",
        timestamp=t0,
    )
    bars_step1 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t0, open=25.0, high=26.0, low=24.0, close=25.0, volume=1_000_000.0),
    }
    runner.step(bars_step1, defensive_triggers=[trigger])

    # Bar 2 to execute resting BUY order
    t1 = t0 + timedelta(days=1)
    bars_step2 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t1, open=25.0, high=26.0, low=24.0, close=25.0, volume=1_000_000.0),
    }
    runner.step(bars_step2)

    # Active defensive position must be bounded by sleeve capacity ($5,000 max for VIXY)
    assert "VIXY" in runner.defensive_positions
    vixy_pos = runner.defensive_positions["VIXY"]
    market_val = vixy_pos.shares * vixy_pos.entry_price
    assert market_val <= 5000.0
    assert market_val > 0.0


def test_ac002_non_overlapping_instrument_universe() -> None:
    """AC-002: Collision between Core and Defensive universes is strictly rejected."""
    cfg = make_approved_config()
    # Attempt to load a defensive universe that overlaps with Core symbol AAPL
    spec_collision = DefensiveInstrumentSpec(
        symbol="AAPL",  # Collision!
        instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
        underlying_index="S&P 500",
        leverage_factor=-1.0,
        max_allocation_of_sleeve_pct=0.50,
        max_holding_period_days=10,
    )
    colliding_universe = DefensiveUniverseConfig(
        approved_instruments=(spec_collision,),
        core_universe_symbols=("AAPL", "MSFT"),
    )
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=colliding_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-002",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.05,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=3,
        rationale="SPY crash -5%",
        timestamp=t0,
    )
    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
    }
    with pytest.raises(CoreDefensiveUniverseCollisionError):
        runner.step(bars, defensive_triggers=[trigger])


def test_ac003_deterministic_trigger_ingestion_and_sizing() -> None:
    """AC-003: Tail-risk trigger signals size deterministic hedge allocations."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-003",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=40.0,
        threshold_value=25.0,
        is_triggered=True,
        severity_level=2,
        rationale="VIX Spike",
        timestamp=t0,
    )
    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t0, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    step_res = runner.step(bars, defensive_triggers=[trigger])
    # An order must have been submitted to broker for VIXY
    assert len(step_res.orders_submitted) == 1
    order_id = step_res.orders_submitted[0]
    broker_order = runner.broker.get_order(order_id)
    assert broker_order is not None
    assert broker_order.symbol == "VIXY"


def test_ac004_guardian_execution_gate_enforcement_for_defense() -> None:
    """AC-004: All defensive intake orders route strictly through Guardian Execution Gate.

    Verifies:
    1. Defensive orders route through submit_through_gate with target_sleeve=BLACK_SWAN.
    2. When Guardian Gate checks fail (e.g. system feed down, emergency stop active),
       defensive intake is blocked and no order is submitted.
    3. Sleeve breach (targeting non-BLACK_SWAN sleeve) fails closed.
    4. Broker-level defensive universe validation blocks direct bypass.
    """
    from toso.defense.capital import enforce_black_swan_intake_safety
    from toso.guardian.models import SystemFeedContext

    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # 1. Normal healthy gate execution allows defensive order submission
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-GATE-OK",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=40.0,
        threshold_value=30.0,
        is_triggered=True,
        severity_level=2,
        rationale="Normal trigger with healthy gate",
        timestamp=t0,
    )
    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t0, open=25.0, high=26.0, low=24.0, close=25.0, volume=1_000_000.0),
    }
    healthy_ctx = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=t0,
        evaluation_timestamp=t0,
    )
    step_res = runner.step(bars, feed_context=healthy_ctx, defensive_triggers=[trigger])
    assert len(step_res.orders_submitted) == 1

    # 2. Gate rejection: when system is not operational, Gate blocks defensive order
    runner2 = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    unhealthy_ctx = SystemFeedContext(
        is_system_operational=False,  # Gate check will fail!
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=t0,
        evaluation_timestamp=t0,
    )
    step_res2 = runner2.step(bars, feed_context=unhealthy_ctx, defensive_triggers=[trigger])
    # Gate blocked submission - order was NOT submitted to broker
    assert len(step_res2.orders_submitted) == 0
    assert len(runner2.broker.get_orders()) == 0

    # 3. Emergency stop active blocks defensive orders through Gate
    runner3 = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    halt_ctx = SystemFeedContext(
        is_system_operational=True,
        is_broker_connected=True,
        is_emergency_stop_active=True,  # Circuit breaker active!
        feed_timestamp=t0,
        evaluation_timestamp=t0,
    )
    step_res3 = runner3.step(bars, feed_context=halt_ctx, defensive_triggers=[trigger])
    assert len(step_res3.orders_submitted) == 0
    assert len(runner3.broker.get_orders()) == 0

    # 4. Sleeve breach check: Black Swan trade targeting CORE sleeve is rejected
    port_snap = runner.get_portfolio_snapshot()
    assessment = evaluate_black_swan_capital_intake(
        portfolio_state=port_snap,
        proposed_market_value=5000.0,
        config=cfg.capital,
        evaluation_timestamp=t0,
        target_sleeve=CapitalSleeve.CORE,  # Breach!
    )
    assert assessment.is_approved is False
    with pytest.raises(BlackSwanSleeveBreachError):
        enforce_black_swan_intake_safety(assessment)

    # 5. Broker defense-in-depth: Broker directly rejects defensive orders for Core symbols
    from toso.broker.models import OrderIntent, OrderSide, OrderType
    from toso.defense.errors import CoreDefensiveUniverseCollisionError

    rogue_intent = OrderIntent(
        symbol="AAPL",  # Core symbol!
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        signal_id="ROGUE-DEF",
        intent_timestamp=t0,
        target_sleeve=CapitalSleeve.BLACK_SWAN,
    )
    with pytest.raises(CoreDefensiveUniverseCollisionError):
        runner.broker.submit_order(rogue_intent)


def test_ac005_defensive_execution_on_paper_broker() -> None:
    """AC-005: PaperBroker processes defensive BUY fills and updates positions."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-005",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.04,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=3,
        rationale="SPY crash",
        timestamp=t0,
    )
    b0 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t0, open=15.0, high=15.5, low=14.5, close=15.0, volume=1_000_000.0),
    }
    runner.step(b0, defensive_triggers=[trigger])

    # Next step fills the order
    t1 = t0 + timedelta(days=1)
    b1 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t1, open=15.0, high=15.5, low=14.5, close=15.0, volume=1_000_000.0),
    }
    runner.step(b1)

    assert "SH" in runner.defensive_positions
    sh_pos = runner.defensive_positions["SH"]
    assert sh_pos.shares > 0
    assert sh_pos.entry_price > 0.0
    broker_pos = runner.broker.get_position("SH")
    assert broker_pos is not None
    assert broker_pos.shares == sh_pos.shares


def test_ac006_deescalation_and_orderly_exit() -> None:
    """AC-006: Systemic normalization signal executes orderly liquidation and logs trade record."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    # 1. Trigger hedge intake
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-006",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.04,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=3,
        rationale="SPY crash",
        timestamp=t0,
    )
    b0 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t0, open=15.0, high=15.5, low=14.5, close=15.0, volume=1_000_000.0),
    }
    runner.step(b0, defensive_triggers=[trigger])

    # 2. Fill order on Day 1
    t1 = t0 + timedelta(days=1)
    b1 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t1, open=15.0, high=18.0, low=14.8, close=17.5, volume=1_000_000.0),
    }
    runner.step(b1)
    assert "SH" in runner.defensive_positions

    # 3. Market normalizes on Day 2: send normalization signal for SH
    t2 = t0 + timedelta(days=2)
    b2 = {
        "AAPL": Bar(timestamp=t2, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t2, open=17.5, high=18.0, low=17.0, close=17.2, volume=1_000_000.0),
    }
    runner.step(b2, normalization_signals=["SH"])

    # 4. Fill exit on Day 3
    t3 = t0 + timedelta(days=3)
    b3 = {
        "AAPL": Bar(timestamp=t3, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t3, open=17.2, high=17.5, low=16.8, close=17.0, volume=1_000_000.0),
    }
    runner.step(b3)

    # Position should be closed and trade recorded
    assert "SH" not in runner.defensive_positions
    def_trades = [t for t in runner.completed_trades if t.symbol == "SH"]
    assert len(def_trades) == 1
    assert def_trades[0].pnl > 0.0  # Profitable hedge exit


def test_ac007_authoritative_multi_sleeve_reconciliation() -> None:
    """AC-007: Portfolio state snapshot captures both Core and Defense, reconciling cleanly."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-007",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=40.0,
        threshold_value=25.0,
        is_triggered=True,
        severity_level=2,
        rationale="VIX Spike",
        timestamp=t0,
    )
    b0 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t0, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    runner.step(b0, defensive_triggers=[trigger])

    t1 = t0 + timedelta(days=1)
    b1 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t1, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    runner.step(b1)

    # Verify authoritative reconciliation
    port_snap = runner._build_portfolio_snapshot(t1)
    broker_pos_snaps = runner.broker.get_position_snapshots(t1)
    rec = reconcile_positions(
        local_positions=port_snap.positions,
        broker_positions=broker_pos_snaps,
        reconciliation_timestamp=t1,
    )
    assert rec.has_material_discrepancy is False
    assert any(p.sleeve == CapitalSleeve.BLACK_SWAN for p in port_snap.positions)


def test_ac008_coexistence_under_market_crash() -> None:
    """AC-008: Under a market crash, Core exits on invalidation while Defense activates."""
    cfg = make_approved_config(fast_ma=2, slow_ma=4, breakout_lookback=2)
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    # Days 0-5: AAPL bull run triggers Core entry
    feed: list[dict[str, Bar]] = []
    prices = [100.0, 101.0, 102.0, 104.0, 107.0, 110.0]
    for i, p in enumerate(prices):
        t = t0 + timedelta(days=i)
        feed.append(
            {
                "AAPL": Bar(
                    timestamp=t, open=p, high=p * 1.02, low=p * 0.98, close=p, volume=10000.0
                ),
                "SH": Bar(
                    timestamp=t,
                    open=15.0,
                    high=15.2,
                    low=14.8,
                    close=15.0,
                    volume=1_000_000.0,
                ),
            }
        )

    # Step through bull run
    for step_b in feed:
        runner.step(step_b)

    # Entry order was executed on bar 6 open (~110)
    assert "AAPL" in runner.core_positions

    # Day 6: Flash crash on AAPL + Volatility spike trigger for SH
    t6 = t0 + timedelta(days=6)
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-CRASH",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.08,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=4,
        rationale="Flash crash -8%",
        timestamp=t6,
    )
    crash_bar = {
        "AAPL": Bar(timestamp=t6, open=110.0, high=111.0, low=60.0, close=65.0, volume=50000.0),
        "SH": Bar(timestamp=t6, open=15.0, high=22.0, low=14.9, close=21.0, volume=1_000_000.0),
    }
    runner.step(crash_bar, defensive_triggers=[trigger])

    # Day 7: Fills execute (AAPL sells on invalidation, SH buys for hedge)
    t7 = t0 + timedelta(days=7)
    day7_bar = {
        "AAPL": Bar(timestamp=t7, open=65.0, high=66.0, low=63.0, close=64.0, volume=20000.0),
        "SH": Bar(timestamp=t7, open=21.0, high=25.0, low=20.5, close=24.0, volume=1_000_000.0),
    }
    runner.step(day7_bar)

    # Core trade was terminated deterministically
    assert "AAPL" not in runner.core_positions
    # Defensive position is active and protecting equity
    assert "SH" in runner.defensive_positions
    assert runner.defensive_positions["SH"].shares > 0


def test_ac009_pure_determinism_and_bit_identical_reproducibility() -> None:
    """AC-009: Two independent runs over identical feeds produce bit-identical receipts."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    feed = [
        {
            "AAPL": Bar(
                timestamp=t0 + timedelta(days=i),
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1000.0,
            ),
            "SH": Bar(
                timestamp=t0 + timedelta(days=i),
                open=15.0,
                high=15.5,
                low=14.5,
                close=15.0,
                volume=1_000_000.0,
            ),
        }
        for i in range(5)
    ]
    triggers = [
        (
            [
                DefensiveTriggerSignal(
                    signal_id=f"TRIG-{i}",
                    trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
                    observed_value=-0.03,
                    threshold_value=-0.02,
                    is_triggered=True,
                    severity_level=2,
                    rationale="Defensive trigger",
                    timestamp=t0 + timedelta(days=i),
                )
            ]
            if i == 0
            else None
        )
        for i in range(5)
    ]

    r1 = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    s1 = r1.run_simulation(feed, defensive_triggers=triggers)

    r2 = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    s2 = r2.run_simulation(feed, defensive_triggers=triggers)

    assert s1.final_equity == s2.final_equity
    assert s1.receipt_checksum == s2.receipt_checksum
    assert len(s1.receipt_checksum) == 64


def test_ac010_fail_closed_on_corrupt_defensive_inputs() -> None:
    """AC-010: Corrupt trigger signals or malformed defensive inputs fail closed."""
    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
    }
    # Non-DefensiveTriggerSignal instance must fail closed
    with pytest.raises(UnknownDefenseInputError):
        runner.step(bars, defensive_triggers=["INVALID_TRIGGER"])  # type: ignore[list-item]
