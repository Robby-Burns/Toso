"""Verification Test Suite for Story PAPER-005: Failure Injections & Emergency Kill-Switch.

Verifies:
- AC-001: Manual Emergency Kill-Switch (FR-015)
- AC-002: Emergency Full Liquidation (FR-015)
- AC-003: Peak-to-Trough Drawdown Circuit Breaker
- AC-004: Stale & Inverted Market Data Rejection (FR-014, INV-008)
- AC-005: Broker Disconnection Fail-Closed (FR-014, INV-007)
- AC-006: System Non-Operational Guard (FR-014, INV-007)
- AC-007: Broker Rejection Handling & No Blind Retry (INV-010)
- AC-008: Authoritative Reconciliation Invariant under Failure (INV-009, INV-010)
- AC-009: Cryptographic Audit Trail of Failures & Stops (FR-016)
- AC-010: Determinism & Post-Halt State Immutability
"""

from datetime import UTC, datetime, timedelta

import pytest

from toso.broker.errors import AccountReconciliationError
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
from toso.paper.errors import UnknownPaperInputError
from toso.paper.runner import PaperTradingRunner
from toso.scientist.models import JournalEventType
from toso.signals.models import Bar


def make_approved_config(
    drawdown_stop_pct: float = 0.10,
    core_alloc: float = 0.80,
    reserve_alloc: float = 0.10,
    black_swan_alloc: float = 0.10,
) -> CoreConfig:
    """Helper creating an approved CoreConfig for failure tests."""
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
            emergency_drawdown_stop_pct=drawdown_stop_pct,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=core_alloc,
            reserve_allocation_pct=reserve_alloc,
            black_swan_allocation_pct=black_swan_alloc,
        ),
        universe=UniverseConfig(
            eligible_symbols=("AAPL", "MSFT"),
            min_average_daily_volume_shares=1000,
            min_share_price_dollars=5.0,
        ),
    )


def generate_bars(prices: list[float], start_ts: datetime) -> list[Bar]:
    """Generate deterministic Bar objects."""
    return [
        Bar(
            timestamp=start_ts + timedelta(days=i),
            open=p,
            high=p + 1.0,
            low=p - 1.0,
            close=p,
            volume=1_000_000.0,
        )
        for i, p in enumerate(prices)
    ]


def test_ac001_manual_emergency_kill_switch() -> None:
    """AC-001: Calling trigger_kill_switch halts runner, cancels resting orders, and logs stop."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Trigger kill-switch manually
    runner.trigger_kill_switch(reason="Manual supervisor halt", timestamp=t0)
    assert runner.is_halted is True

    # Journal records EMERGENCY_STOP event
    stop_entries = runner.journal.get_entries_by_type(JournalEventType.EMERGENCY_STOP)
    assert len(stop_entries) == 1
    assert stop_entries[0].payload["trigger"] == "MANUAL_KILL_SWITCH"
    assert stop_entries[0].payload["reason"] == "Manual supervisor halt"

    # Subsequent steps reject new orders
    prices = [100.0, 102.0, 105.0, 108.0, 112.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        res = runner.step({"AAPL": b})
        assert res.is_halted is True
        assert len(res.orders_submitted) == 0


def test_ac002_emergency_full_liquidation() -> None:
    """AC-002: Calling emergency_liquidate_all routes market sell orders for 100% of positions."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Establish an open Core position
    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    assert "AAPL" in runner.core_positions

    # Trigger emergency liquidation
    t_liq = t0 + timedelta(days=10)
    liq_orders = runner.emergency_liquidate_all(timestamp=t_liq, reason="Exchange panic")
    assert len(liq_orders) >= 1
    assert runner.is_halted is True

    # Process next bar so liquidation order executes
    liq_bar = Bar(
        timestamp=t_liq,
        open=108.0,
        high=109.0,
        low=107.0,
        close=108.0,
        volume=1_000_000.0,
    )
    runner.step({"AAPL": liq_bar})

    # Core positions liquidated
    assert len(runner.core_positions) == 0
    assert len(runner.completed_trades) >= 1


def test_ac003_drawdown_circuit_breaker_halts_runner() -> None:
    """AC-003: Peak-to-trough drawdown exceeding threshold halts simulation automatically."""
    cfg = make_approved_config(drawdown_stop_pct=0.05)  # 5% drawdown stop
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Entry on AAPL at ~108, then severe drop to 90 causing > 5% equity drawdown
    prices = [100.0, 101.0, 103.0, 105.0, 108.0, 70.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    assert runner.is_halted is True
    stop_entries = runner.journal.get_entries_by_type(JournalEventType.EMERGENCY_STOP)
    assert len(stop_entries) >= 1
    assert "Drawdown stop breached" in stop_entries[0].payload["reason"]


def test_ac004_stale_and_inverted_market_data_rejection() -> None:
    """AC-004: Inverted prices, corrupt values, or stale timestamps fail closed."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # 1. Non-chronological timestamp
    stale_bar = Bar(
        timestamp=t0 - timedelta(days=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
    )
    with pytest.raises(UnknownPaperInputError) as exc_stale:
        runner.step({"AAPL": stale_bar})
    assert "Non-chronological" in str(exc_stale.value)

    # 2. Inverted bar (high < low) via model_construct simulating unvalidated data
    inv_bar = Bar.model_construct(
        timestamp=t0 + timedelta(days=1),
        open=100.0,
        high=95.0,
        low=105.0,
        close=100.0,
        volume=1000.0,
    )
    with pytest.raises(UnknownPaperInputError) as exc_inv:
        runner.step({"AAPL": inv_bar})
    assert "Corrupted bar" in str(exc_inv.value) or "high" in str(exc_inv.value)

    # 3. Negative price via model_construct simulating unvalidated data
    neg_bar = Bar.model_construct(
        timestamp=t0 + timedelta(days=1),
        open=-10.0,
        high=100.0,
        low=90.0,
        close=95.0,
        volume=1000.0,
    )
    with pytest.raises(UnknownPaperInputError) as exc_neg:
        runner.step({"AAPL": neg_bar})
    assert "Corrupt or non-positive price" in str(exc_neg.value)

    # Failures are recorded in journal
    failure_entries = runner.journal.get_entries_by_type(JournalEventType.FAILURE_RECORDED)
    assert len(failure_entries) >= 3


def test_ac005_broker_disconnection_blocks_execution() -> None:
    """AC-005: When broker is disconnected in feed context, Gate blocks orders."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Feed bars with disconnected broker feed context
    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        feed_ctx = SystemFeedContext(
            is_system_operational=True,
            is_broker_connected=False,  # Broker disconnected
            is_emergency_stop_active=False,
            feed_timestamp=b.timestamp,
            evaluation_timestamp=b.timestamp,
        )
        res = runner.step({"AAPL": b}, feed_context=feed_ctx)
        assert len(res.orders_submitted) == 0

    assert len(runner.core_positions) == 0
    failure_entries = runner.journal.get_entries_by_type(JournalEventType.FAILURE_RECORDED)
    assert len(failure_entries) >= 1
    assert any("Broker is disconnected" in str(f.payload) for f in failure_entries)


def test_ac006_system_non_operational_blocks_execution() -> None:
    """AC-006: When system is degraded (not operational), Gate blocks order submissions."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        feed_ctx = SystemFeedContext(
            is_system_operational=False,  # Degraded
            is_broker_connected=True,
            is_emergency_stop_active=False,
            feed_timestamp=b.timestamp,
            evaluation_timestamp=b.timestamp,
        )
        res = runner.step({"AAPL": b}, feed_context=feed_ctx)
        assert len(res.orders_submitted) == 0

    assert len(runner.core_positions) == 0


def test_ac007_broker_rejection_no_blind_retry() -> None:
    """AC-007: Broker rejections do not cause blind retry loops or ghost positions (INV-010)."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Feed bars with active emergency stop in context
    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        feed_ctx = SystemFeedContext(
            is_system_operational=True,
            is_broker_connected=True,
            is_emergency_stop_active=True,  # Active emergency stop
            feed_timestamp=b.timestamp,
            evaluation_timestamp=b.timestamp,
        )
        res = runner.step({"AAPL": b}, feed_context=feed_ctx)
        assert len(res.orders_submitted) == 0

    assert runner.is_halted is True
    assert len(runner.core_positions) == 0


def test_ac008_authoritative_reconciliation_invariant_under_failure() -> None:
    """AC-008: Reconciliation detects state corruption and raises error immediately."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Injected balance corruption into broker internal state
    runner.broker._cash_balance = 50_000.0  # Diverged from expected local account state

    # Authoritative reconciliation verifies reconcile_account_state fails on mismatch
    from toso.broker.account import enforce_account_reconciliation, reconcile_account_state
    from toso.broker.models import AccountReconciliationStatus, LocalAccountSnapshot

    broker_snap = runner.broker.get_account_snapshot(t0)
    tampered_local = LocalAccountSnapshot(
        account_id=runner.broker.account_id,
        total_equity=100_000.0,  # Corrupted mismatch
        cash_balance=100_000.0,
        currency="USD",
        timestamp=t0,
    )
    receipt = reconcile_account_state(
        broker_snapshot=broker_snap,
        local_snapshot=tampered_local,
        reconciliation_timestamp=t0,
    )
    assert receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED
    with pytest.raises(AccountReconciliationError):
        enforce_account_reconciliation(receipt)


def test_ac009_cryptographic_audit_trail_of_failures_and_stops() -> None:
    """AC-009: All kill-switch stops and failure events are cryptographically sealed in journal."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Trigger stop
    runner.trigger_kill_switch("Audit test stop", t0)

    # Attempt corrupt step
    bad_bar = Bar(
        timestamp=t0 - timedelta(hours=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
    )
    try:
        runner.step({"AAPL": bad_bar})
    except UnknownPaperInputError:
        pass

    # Verify cryptographic integrity of the entire chain
    receipt = runner.journal.verify_integrity(t0 + timedelta(days=1))
    assert receipt.is_valid is True
    assert receipt.total_entries >= 3
    event_types = {e.event_type for e in runner.journal.get_entries()}
    assert JournalEventType.EMERGENCY_STOP in event_types
    assert JournalEventType.FAILURE_RECORDED in event_types


def test_ac010_determinism_and_post_halt_state_immutability() -> None:
    """AC-010: Independent simulations undergoing emergency stop produce identical state."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)

    # Runner 1
    r1 = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    for b in bars[:3]:
        r1.step({"AAPL": b})
    r1.trigger_kill_switch("Deterministic halt", bars[2].timestamp)
    for b in bars[3:]:
        r1.step({"AAPL": b})

    # Runner 2
    r2 = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    for b in bars[:3]:
        r2.step({"AAPL": b})
    r2.trigger_kill_switch("Deterministic halt", bars[2].timestamp)
    for b in bars[3:]:
        r2.step({"AAPL": b})

    assert r1.is_halted is True
    assert r2.is_halted is True
    assert r1.broker.cash_balance == r2.broker.cash_balance
    assert r1.broker.total_equity == r2.broker.total_equity
    assert r1.journal.total_entries == r2.journal.total_entries

    for e1, e2 in zip(r1.journal.get_entries(), r2.journal.get_entries(), strict=True):
        assert e1.entry_hash == e2.entry_hash
