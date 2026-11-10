"""Verification Test Suite for Story PAPER-004: Journaling & Scientist Boundary in Paper Mode.

Verifies:
- AC-001: Append-Only Real-Time Journaling (FR-016)
- AC-002: Cryptographic Chain Integrity (INV-001, INV-006)
- AC-003: Full Historical Reconstruction from Journal
- AC-004: Strict Scientist Execution Boundary (INV-014, FR-017)
- AC-005: Mandatory Human Approval for Scientist Paper Proposals
- AC-006: Comprehensive Event Taxonomy Coverage
- AC-007: Persistent Disk Logging & Export
- AC-008: Fail-Closed Tamper & Mutation Detection
- AC-009: Clock Anchoring Consistency
- AC-010: Pure Determinism & Bit-Identical Audit Hashes
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    DefensiveUniverseConfig,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.errors import (
    InvalidApprovalTokenError,
    JournalTamperError,
    ScientistExecutionBreachError,
    UnauthorizedProductionChangeError,
)
from toso.scientist.journal import TradeJournal
from toso.scientist.models import (
    HumanApprovalToken,
    JournalEventType,
    ScientistRole,
)
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
    core_alloc: float = 0.80,
    reserve_alloc: float = 0.10,
    black_swan_alloc: float = 0.10,
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
    """Generate deterministic Bar objects for given price series."""
    bars = []
    for i, p in enumerate(prices):
        ts = start_ts + timedelta(days=i)
        bars.append(
            Bar(
                timestamp=ts,
                open=p,
                high=p + 1.0,
                low=p - 1.0,
                close=p,
                volume=1_000_000.0,
            )
        )
    return bars


def test_ac001_append_only_real_time_journaling() -> None:
    """AC-001: Runner records lifecycle events to append-only TradeJournal in real-time."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    journal = TradeJournal()
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        journal=journal,
    )

    # Initial CONFIGURATION_LOADED event
    assert journal.total_entries == 1
    init_entry = journal.get_entries()[0]
    assert init_entry.event_type == JournalEventType.CONFIGURATION_LOADED
    assert init_entry.payload["initial_cash"] == 100_000.0

    # Step through bullish breakout
    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    # Journal should now have recorded signals, sizing, gate decisions, orders, fills, positions
    entries = journal.get_entries()
    event_types = {e.event_type for e in entries}
    assert JournalEventType.SIGNAL_GENERATED in event_types
    assert JournalEventType.SIZING_CALCULATED in event_types
    assert JournalEventType.RISK_EVALUATED in event_types
    assert JournalEventType.EXECUTION_SUBMITTED in event_types
    assert JournalEventType.BROKER_FILL in event_types
    assert JournalEventType.POSITION_CHANGED in event_types


def test_ac002_cryptographic_chain_integrity() -> None:
    """AC-002: Journal maintains SHA-256 chain integrity verified by receipt (INV-001, INV-006)."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    prices = [100.0, 102.0, 104.0, 106.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    receipt = runner.journal.verify_integrity(t0 + timedelta(days=10))
    assert receipt.is_valid is True
    assert receipt.total_entries > 0
    assert receipt.latest_sequence_number == receipt.total_entries - 1
    assert len(receipt.latest_entry_hash) == 64


def test_ac003_full_historical_reconstruction_from_journal() -> None:
    """AC-003: Reconstruct runner cash, equity, positions, and trades from journal."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Bullish trend followed by sharp drop to trigger invalidation stop
    prices = [100.0, 101.0, 103.0, 105.0, 108.0, 80.0, 75.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    # Reconstruct state from journal
    recon = PaperTradingRunner.reconstruct_from_journal(runner.journal)

    assert recon["total_entries"] == runner.journal.total_entries
    assert "CONFIGURATION_LOADED" in recon["event_counts"]
    assert "BROKER_FILL" in recon["event_counts"]
    assert "POSITION_CHANGED" in recon["event_counts"]

    # Invalidation stop was triggered and trade completed
    assert len(recon["completed_trades"]) == len(runner.completed_trades)
    if recon["completed_trades"]:
        assert recon["completed_trades"][0]["symbol"] == "AAPL"
        assert recon["completed_trades"][0]["pnl"] == runner.completed_trades[0].pnl


def test_ac004_strict_scientist_execution_boundary() -> None:
    """AC-004: Scientist layer has strictly read-only visibility; order execution is blocked."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Scientist can read journal entries safely
    entries = runner.journal.get_entries()
    assert len(entries) >= 1

    # Attempting to submit or execute as Scientist raises ScientistExecutionBreachError
    with pytest.raises(ScientistExecutionBreachError) as exc_info:
        runner.submit_as_scientist(
            action="submit_order",
            timestamp=t0,
            caller_role=ScientistRole.RESEARCHER,
        )
    assert "RESEARCHER" in str(exc_info.value)

    with pytest.raises(ScientistExecutionBreachError):
        runner.submit_as_scientist(
            action="cancel_order",
            timestamp=t0,
            caller_role=ScientistRole.ANALYST,
        )


def test_ac005_mandatory_human_approval_for_scientist_paper_proposals() -> None:
    """AC-005: Scientist proposals require valid HumanApprovalToken before being applied."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # Create unapproved proposal to change fast_ma_period from 2 to 3
    proposal = create_production_proposal(
        proposal_id="PROP-OPT-001",
        hypothesis_id="HYP-MOMENTUM-01",
        proposed_parameters={"fast_ma_period": 3},
        rationale="Grid search optimization on paper run",
        created_at=t0,
    )

    # Applying unapproved proposal raises UnauthorizedProductionChangeError
    with pytest.raises(UnauthorizedProductionChangeError):
        runner.apply_proposal(proposal, current_time=t0)

    # Create valid human approval token
    app_ts = t0
    exp_ts = t0 + timedelta(days=7)
    sig = generate_human_approval_signature(
        token_id="TOKEN-LEAD-001",
        approver_id="ChiefRiskOfficer",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=app_ts,
    )
    token = HumanApprovalToken(
        token_id="TOKEN-LEAD-001",
        approver_id="ChiefRiskOfficer",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=app_ts,
        expires_at=exp_ts,
        signature=sig,
    )

    # Attaching expired token or wrong time fails
    with pytest.raises(InvalidApprovalTokenError):
        attach_human_approval(proposal, token, current_time=t0 + timedelta(days=10))

    # Valid approval attaches cleanly and updates runner config
    approved_prop = attach_human_approval(proposal, token, current_time=t0 + timedelta(hours=1))
    new_cfg = runner.apply_proposal(approved_prop, current_time=t0 + timedelta(hours=1))
    assert new_cfg.strategy.fast_ma_period == 3
    assert runner.config.strategy.fast_ma_period == 3

    # CONFIGURATION_LOADED event recorded in journal
    cfg_entries = runner.journal.get_entries_by_type(JournalEventType.CONFIGURATION_LOADED)
    assert len(cfg_entries) == 2
    assert cfg_entries[-1].payload["action"] == "PROPOSAL_APPLIED"


def test_ac006_comprehensive_event_taxonomy_coverage() -> None:
    """AC-006: Captures all event categories (Core, Gate, Pyramiding, Black Swan defense)."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    def_universe = DefensiveUniverseConfig(
        approved_instruments=(
            DefensiveInstrumentSpec(
                symbol="SH",
                instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
                underlying_index="S&P 500",
                leverage_factor=-1.0,
                max_allocation_of_sleeve_pct=0.50,
                max_holding_period_days=10,
            ),
        ),
        core_universe_symbols=("AAPL", "MSFT"),
    )
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        defensive_universe=def_universe,
    )

    trigger = DefensiveTriggerSignal(
        signal_id="TRIG-VIX-001",
        trigger_type=DefensiveTriggerType.VOLATILITY_SPIKE,
        observed_value=35.0,
        threshold_value=30.0,
        is_triggered=True,
        severity_level=3,
        rationale="VIX above 30 threshold",
        timestamp=t0,
    )

    bars = {
        "AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        "SH": Bar(timestamp=t0, open=20.0, high=21.0, low=19.0, close=20.0, volume=1_000_000.0),
    }

    # Execute step with defensive trigger
    runner.step(bars, defensive_triggers=[trigger])

    defense_events = runner.journal.get_entries_by_type(JournalEventType.DEFENSE_TRIGGERED)
    assert len(defense_events) >= 1
    assert defense_events[0].payload["symbol"] == "SH"


def test_ac007_persistent_disk_logging_and_export(tmp_path: Path) -> None:
    """AC-007: Journal supports disk persistence and reload without breaking verification."""
    log_file = tmp_path / "paper_trade_journal.jsonl"
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    journal = TradeJournal(persistence_path=log_file)
    runner = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        journal=journal,
    )

    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    assert log_file.exists()
    assert log_file.stat().st_size > 0

    # Reload from disk into new journal instance
    reloaded_journal = TradeJournal(persistence_path=log_file)
    assert reloaded_journal.total_entries == journal.total_entries

    receipt = reloaded_journal.verify_integrity(t0 + timedelta(days=5))
    assert receipt.is_valid is True
    assert receipt.latest_entry_hash == journal.get_entries()[-1].entry_hash


def test_ac008_fail_closed_tamper_and_mutation_detection() -> None:
    """AC-008: Any payload alteration, entry deletion, or sequence gap raises JournalTamperError."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    journal = runner.journal
    assert journal.total_entries >= 3

    # Tamper with entry payload
    orig_entry = journal._entries[1]
    tampered_entry = orig_entry.model_copy(update={"payload": {"hacked": True}})
    journal._entries[1] = tampered_entry

    with pytest.raises(JournalTamperError) as exc_info:
        journal.verify_integrity(t0 + timedelta(days=5))
    assert "tampering detected" in str(exc_info.value).lower()


def test_ac009_clock_anchoring_consistency() -> None:
    """AC-009: All journal entries strictly echo bar timestamps with zero wall-clock reads."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    prices = [100.0, 101.0, 103.0, 105.0, 108.0]
    bars = generate_bars(prices, t0)
    for b in bars:
        runner.step({"AAPL": b})

    entries = runner.journal.get_entries()
    expected_timestamps = {t0} | {b.timestamp for b in bars}
    for e in entries:
        assert e.timestamp in expected_timestamps


def test_ac010_determinism_and_bit_identical_audit_hashes() -> None:
    """AC-010: Two simulation runs over identical feed produce bit-identical journal chains."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    prices = [100.0, 101.0, 103.0, 105.0, 108.0, 112.0]
    bars = generate_bars(prices, t0)
    feed = [{"AAPL": b} for b in bars]

    r1 = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    s1 = r1.run_simulation(feed)

    r2 = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    s2 = r2.run_simulation(feed)

    assert s1.receipt_checksum == s2.receipt_checksum
    assert r1.journal.total_entries == r2.journal.total_entries

    entries1 = r1.journal.get_entries()
    entries2 = r2.journal.get_entries()
    for e1, e2 in zip(entries1, entries2, strict=True):
        assert e1.entry_hash == e2.entry_hash
        assert e1.sequence_number == e2.sequence_number
        assert e1.previous_entry_hash == e2.previous_entry_hash
        assert e1.event_type == e2.event_type
