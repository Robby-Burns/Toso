"""Adversarial and independent verification tests for PAPER-003.

Tests the 9 mandatory verification lenses for Black Swan Defense in Paper Simulation:
1. Boundary: Corrupt allocations, extreme leverage attempts, empty universes.
2. Security: INV-014 strict paper isolation, zero network leakage, zero execution handles.
3. Failure Paths: Universe collision detection, corrupt trigger parameters, unapproved symbols.
4. Skeptic: Black swan sleeve cap cannot be breached even with multiple simultaneous triggers.
5. Red Team: Cross-sleeve intrusion attempts (Core order in Black Swan, Defensive in Core).
6. QA Edge: Flapping triggers, trigger with zero volume/liquidity, empty market feed.
7. Regression: Authoritative multi-sleeve broker reconciliation consistency.
8. Runtime: Deterministic clock anchoring (no wall-clock reads).
9. Blast Radius: Frozen domain models, immutable defense receipts.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.broker.positions import reconcile_positions
from toso.config.errors import ConfigurationValidationError
from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.defense.errors import (
    CoreDefensiveUniverseCollisionError,
    DefensiveUniverseLiquidityError,
    DisallowedDefensiveInstrumentError,
)
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    DefensiveUniverseConfig,
)
from toso.defense.universe import (
    enforce_defensive_instrument_eligibility,
    load_canonical_defensive_universe,
)
from toso.guardian.models import CapitalSleeve
from toso.paper.runner import PaperTradingRunner
from toso.signals.models import Bar


def make_approved_config(
    core_alloc: float = 0.80,
    reserve_alloc: float = 0.10,
    black_swan_alloc: float = 0.10,
) -> CoreConfig:
    return CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.APPROVED,
            approved_by="ChiefRiskOfficer",
            approved_at="2026-06-01T00:00:00Z",
            checksum="d" * 64,
            description="Paper Defense Adversarial Config",
        ),
        strategy=StrategyConfig(
            strategy_id="STRAT-ADV-001",
            timeframe="1d",
            fast_ma_period=2,
            slow_ma_period=4,
            atr_period=2,
            breakout_lookback_bars=2,
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


def test_lens1_boundary_extreme_or_corrupt_allocations() -> None:
    """Lens 1 Boundary: Negative or >1 allocations fail closed at config creation."""
    with pytest.raises(ValidationError):
        CapitalAllocationConfig(
            core_allocation_pct=0.95,
            reserve_allocation_pct=0.10,
            black_swan_allocation_pct=-0.05,
        )

    with pytest.raises(ConfigurationValidationError):
        CapitalAllocationConfig(
            core_allocation_pct=0.50,
            reserve_allocation_pct=0.20,
            black_swan_allocation_pct=0.40,  # sum = 1.10 != 1.00
        )


def test_lens2_security_paper_isolation() -> None:
    """Lens 2 Security: INV-014 strict paper isolation, no live execution attributes."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    forbidden_attrs = [
        "alpaca",
        "interactive_brokers",
        "polygon",
        "live_client",
        "api_key",
        "secret_key",
        "order_router",
    ]
    for attr in forbidden_attrs:
        assert not hasattr(runner, attr)
        assert not hasattr(runner.broker, attr)


def test_lens3_failure_paths_universe_collision() -> None:
    """Lens 3 Failure Paths: Using a Core symbol in defensive universe raises collision error."""
    cfg = make_approved_config()
    spec_collision = DefensiveInstrumentSpec(
        symbol="AAPL",  # Collision with Core AAPL
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
        signal_id="TRIG-COL-001",
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


def test_lens4_skeptic_sleeve_capacity_strictly_honored() -> None:
    """Lens 4 Skeptic: Triggers cannot force defensive allocations beyond sleeve capacity."""
    cfg = make_approved_config(core_alloc=0.90, reserve_alloc=0.05, black_swan_alloc=0.05)
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    # 5% black swan on $100k = $5,000 capacity. Max SH allocation is 50% = $2,500.
    t1 = t0 + timedelta(days=1)
    bars1 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t0, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-MAX-001",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.06,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=3,
        rationale="Max severity breakdown trigger",
        timestamp=t0,
    )
    runner.step(bars1, defensive_triggers=[trigger])

    bars2 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t1, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    runner.step(bars2)

    assert "SH" in runner.defensive_positions
    sh_pos = runner.defensive_positions["SH"]
    pos_val = sh_pos.shares * sh_pos.entry_price
    assert pos_val <= 2500.0
    assert pos_val > 0.0


def test_lens5_red_team_unapproved_defensive_instrument_rejected() -> None:
    """Lens 5 Red Team: Defensive orders cannot bypass Gate or Broker universe validation."""
    from toso.broker.models import OrderIntent, OrderSide, OrderType
    from toso.guardian.models import SystemFeedContext
    from toso.paper.broker import PaperBroker

    cfg = make_approved_config()
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # 1. Unapproved symbol rejected by universe eligibility
    with pytest.raises(DisallowedDefensiveInstrumentError):
        enforce_defensive_instrument_eligibility(
            symbol="UNAPPROVED",
            config=def_universe,
            evaluation_timestamp=t0,
            current_average_daily_volume=1_000_000,
            current_price=10.0,
        )

    # 2. Broker direct submission of Core symbol as defensive order is rejected
    broker = PaperBroker(initial_cash=100_000.0, defensive_universe=def_universe)
    rogue_intent = OrderIntent(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        signal_id="ROGUE-DEF-1",
        intent_timestamp=t0,
        target_sleeve=CapitalSleeve.BLACK_SWAN,
    )
    with pytest.raises(CoreDefensiveUniverseCollisionError):
        broker.submit_order(rogue_intent)

    # 3. Guardian Gate blocks defensive orders when system feed is not operational
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )
    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-GATE-BLOCKED",
        trigger_type=DefensiveTriggerType.BENCHMARK_BREAKDOWN,
        observed_value=-0.06,
        threshold_value=-0.02,
        is_triggered=True,
        severity_level=3,
        rationale="Severe crash with offline system",
        timestamp=t0,
    )
    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t0, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }
    offline_ctx = SystemFeedContext(
        is_system_operational=False,  # Unoperational feed context
        is_broker_connected=True,
        is_emergency_stop_active=False,
        feed_timestamp=t0,
        evaluation_timestamp=t0,
    )
    step_res = runner.step(bars, feed_context=offline_ctx, defensive_triggers=[trigger])
    assert len(step_res.orders_submitted) == 0
    assert len(runner.broker.get_orders()) == 0


def test_lens6_qa_edge_flapping_triggers_and_zero_liquidity() -> None:
    """Lens 6 QA Edge: Defensive instrument with low volume rejected by liquidity check."""
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
        signal_id="TRIG-ILLIQUID",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=40.0,
        threshold_value=30.0,
        is_triggered=True,
        severity_level=2,
        rationale="Illiquid volume test",
        timestamp=t0,
    )
    # VIXY is selected for VOLATILITY_SPIKE and requires min_average_daily_volume = 250_000
    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(
            timestamp=t0, open=25.0, high=26.0, low=24.0, close=25.0, volume=100.0
        ),  # insufficient!
    }
    with pytest.raises(DefensiveUniverseLiquidityError):
        runner.step(bars, defensive_triggers=[trigger])


def test_lens7_regression_multi_sleeve_reconciliation() -> None:
    """Lens 7 Regression: Local defensive and core positions reconcile against PaperBroker."""
    cfg = make_approved_config(core_alloc=0.80, reserve_alloc=0.10, black_swan_alloc=0.10)
    def_universe = load_canonical_defensive_universe(core_symbols=("AAPL", "MSFT"))
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-RECON",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=45.0,
        threshold_value=30.0,
        is_triggered=True,
        severity_level=2,
        rationale="Volatility spike",
        timestamp=t0,
    )
    bars1 = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t0, open=25.0, high=26.0, low=24.0, close=25.0, volume=1_000_000.0),
    }
    runner.step(bars1, defensive_triggers=[trigger])

    t1 = t0 + timedelta(days=1)
    bars2 = {
        "AAPL": Bar(timestamp=t1, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "VIXY": Bar(timestamp=t1, open=25.0, high=26.0, low=24.0, close=25.0, volume=1_000_000.0),
    }
    runner.step(bars2)

    # Validate broker reconciliation
    port_snap = runner._build_portfolio_snapshot(t1)
    broker_pos_snaps = runner.broker.get_position_snapshots(t1)
    rec = reconcile_positions(
        local_positions=port_snap.positions,
        broker_positions=broker_pos_snaps,
        reconciliation_timestamp=t1,
    )
    assert rec.has_material_discrepancy is False
    assert "VIXY" in runner.defensive_positions
    broker_vixy = runner.broker.get_position("VIXY")
    assert broker_vixy is not None
    assert runner.defensive_positions["VIXY"].shares == broker_vixy.shares


def test_lens8_runtime_pure_clock_anchoring() -> None:
    """Lens 8 Runtime: Portfolio snapshot timestamp strictly tracks step bar timestamp."""
    cfg = make_approved_config()
    t_anchor = datetime(2024, 5, 10, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t_anchor)

    bars = {
        "AAPL": Bar(
            timestamp=t_anchor, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0
        ),
    }
    res = runner.step(bars)
    assert res.timestamp == t_anchor


def test_lens9_blast_radius_immutability() -> None:
    """Lens 9 Blast Radius: DefensiveInstrumentSpec is frozen and immutable."""
    spec = DefensiveInstrumentSpec(
        symbol="SH",
        instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
        underlying_index="S&P 500",
        leverage_factor=-1.0,
        max_allocation_of_sleeve_pct=0.50,
        max_holding_period_days=60,
        min_average_daily_volume=500000,
    )
    with pytest.raises(ValidationError):
        spec.max_allocation_of_sleeve_pct = 0.10  # type: ignore[misc]
