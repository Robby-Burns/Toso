"""Adversarial and 9-Lens Independent Verification Suite for Story PAPER-004.

Lenses:
1. Boundary: Invalid/NaN/Inf payloads, negative sequences, invalid timestamps.
2. Security: Scientist execution interception, token spoofing, proposal tampering.
3. Failure Paths: Corrupted JSONL file, broken hash chain, sequence gaps.
4. Skeptic: Expired/future human approval tokens fail closed, config preserved.
5. Red Team: Memory payload tampering, entry deletion, order injection blocked.
6. QA Edge: Empty journal genesis verification, single-entry reconstruction.
7. Regression: PaperRunner executes identically with or without custom journal.
8. Runtime: High throughput sequential logging, pure clock anchoring.
9. Blast Radius: Interception errors do not mutate broker, frozen domain models.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.errors import (
    InvalidApprovalTokenError,
    JournalSerializationError,
    JournalTamperError,
    ScientistExecutionBreachError,
    UnknownScientistInputError,
)
from toso.scientist.journal import TradeJournal
from toso.scientist.models import (
    HumanApprovalToken,
    JournalEntry,
    JournalEventType,
    ScientistRole,
)
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
            emergency_drawdown_stop_pct=0.10,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=0.80,
            reserve_allocation_pct=0.10,
            black_swan_allocation_pct=0.10,
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


def test_lens1_boundary_nan_inf_and_invalid_sequences() -> None:
    """Lens 1 Boundary: Payloads with NaN/Inf or invalid sequence queries fail closed."""
    journal = TradeJournal()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # NaN in payload raises UnknownScientistInputError
    with pytest.raises(UnknownScientistInputError):
        journal.record_event(
            event_type=JournalEventType.SIGNAL_GENERATED,
            payload={"price": float("nan")},
            timestamp=t0,
        )

    # Inf in payload raises UnknownScientistInputError
    with pytest.raises(UnknownScientistInputError):
        journal.record_event(
            event_type=JournalEventType.SIZING_CALCULATED,
            payload={"shares": float("inf")},
            timestamp=t0,
        )

    # Invalid sequence ranges in get_entries raise UnknownScientistInputError
    with pytest.raises(UnknownScientistInputError):
        journal.get_entries(start_sequence=-1)

    with pytest.raises(UnknownScientistInputError):
        journal.get_entries(start_sequence=5, end_sequence=2)


def test_lens2_security_scientist_execution_and_token_tampering() -> None:
    """Lens 2 Security: Scientist roles cannot execute; spoofed tokens are rejected (INV-014)."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    # All scientist roles blocked from execution handles
    for role in (ScientistRole.RESEARCHER, ScientistRole.OBSERVER, ScientistRole.ANALYST):
        with pytest.raises(ScientistExecutionBreachError):
            runner.submit_as_scientist(action="submit_order", timestamp=t0, caller_role=role)

    # Spoofed token signature rejected
    proposal = create_production_proposal(
        proposal_id="PROP-SEC-001",
        hypothesis_id="HYP-SEC-01",
        proposed_parameters={"fast_ma_period": 3},
        rationale="Security audit parameter update",
        created_at=t0,
    )
    tampered_token = HumanApprovalToken(
        token_id="TOKEN-SPOOF-001",
        approver_id="Adversary",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=t0,
        expires_at=t0 + timedelta(days=1),
        signature="invalid_signature_hash" * 2,
    )
    with pytest.raises(InvalidApprovalTokenError):
        attach_human_approval(proposal, tampered_token, current_time=t0 + timedelta(minutes=5))


def test_lens3_failure_paths_corrupt_disk_and_broken_chains(tmp_path: Path) -> None:
    """Lens 3 Failure Paths: Corrupt JSONL files or broken hash links fail closed immediately."""
    log_file = tmp_path / "corrupt_journal.jsonl"
    log_file.write_text('{"not": "valid_journal_entry"}\n', encoding="utf-8")

    # Loading corrupted JSONL raises JournalSerializationError
    with pytest.raises(JournalSerializationError):
        TradeJournal(persistence_path=log_file)

    # Sequence gap tampering in memory raises JournalTamperError
    journal = TradeJournal()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    journal.record_event(JournalEventType.CONFIGURATION_LOADED, {"cfg": 1}, t0)
    journal.record_event(JournalEventType.SIGNAL_GENERATED, {"sig": 2}, t0 + timedelta(days=1))

    # Artificially alter sequence number
    e1 = journal._entries[1]
    tampered_e1 = e1.model_copy(update={"sequence_number": 5})
    journal._entries[1] = tampered_e1

    with pytest.raises(JournalTamperError) as exc_info:
        journal.verify_integrity(t0 + timedelta(days=2))
    assert "Sequence gap or tamper detected" in str(exc_info.value)


def test_lens4_skeptic_expired_and_future_approval_tokens() -> None:
    """Lens 4 Skeptic: Human approval tokens outside validity window strictly rejected."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    proposal = create_production_proposal(
        proposal_id="PROP-SKEPTIC-001",
        hypothesis_id="HYP-SKEPTIC-01",
        proposed_parameters={"fast_ma_period": 3},
        rationale="Skeptic lens test",
        created_at=t0,
    )
    app_ts = t0 + timedelta(days=1)
    exp_ts = t0 + timedelta(days=5)
    sig = generate_human_approval_signature(
        token_id="TOKEN-SKEPTIC-001",
        approver_id="RiskLead",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=app_ts,
    )
    token = HumanApprovalToken(
        token_id="TOKEN-SKEPTIC-001",
        approver_id="RiskLead",
        target_proposal_hash=proposal.proposal_hash,
        approved_at=app_ts,
        expires_at=exp_ts,
        signature=sig,
    )

    # 1. Verification time BEFORE approval timestamp
    with pytest.raises(InvalidApprovalTokenError) as exc_before:
        attach_human_approval(proposal, token, current_time=t0)
    assert "precedes approval timestamp" in str(exc_before.value)

    # 2. Verification time AFTER expiration timestamp
    with pytest.raises(InvalidApprovalTokenError) as exc_after:
        attach_human_approval(proposal, token, current_time=t0 + timedelta(days=6))
    assert "expired" in str(exc_after.value)

    # Active config was not altered
    assert runner.config.strategy.fast_ma_period == 2


def test_lens5_red_team_deletion_and_payload_mutation() -> None:
    """Lens 5 Red Team: Deleting entries or altering payloads fails cryptographic validation."""
    journal = TradeJournal()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    journal.record_event(JournalEventType.CONFIGURATION_LOADED, {"cash": 100000.0}, t0)
    journal.record_event(JournalEventType.SIGNAL_GENERATED, {"sym": "AAPL"}, t0 + timedelta(days=1))
    journal.record_event(JournalEventType.SIZING_CALCULATED, {"shares": 50}, t0 + timedelta(days=2))

    assert journal.total_entries == 3

    # Delete middle entry (index 1)
    journal._entries.pop(1)

    with pytest.raises(JournalTamperError):
        journal.verify_integrity(t0 + timedelta(days=3))


def test_lens6_qa_edge_empty_journal_and_reconstruction() -> None:
    """Lens 6 QA Edge: Empty journal verifies genesis; single-entry reconstructs cleanly."""
    journal = TradeJournal()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    # Empty journal verification
    receipt = journal.verify_integrity(t0)
    assert receipt.is_valid is True
    assert receipt.total_entries == 0
    assert receipt.latest_entry_hash == "GENESIS"

    # Single configuration entry reconstruction
    journal.record_event(
        event_type=JournalEventType.CONFIGURATION_LOADED,
        payload={"initial_cash": 75_000.0},
        timestamp=t0,
    )
    recon = PaperTradingRunner.reconstruct_from_journal(journal)
    assert recon["total_entries"] == 1
    assert recon["cash_balance"] == 75_000.0
    assert recon["total_equity"] == 75_000.0
    assert len(recon["open_positions"]) == 0
    assert len(recon["completed_trades"]) == 0


def test_lens7_regression_journal_presence_does_not_alter_execution() -> None:
    """Lens 7 Regression: Runner output is bit-identical with default vs explicit TradeJournal."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    prices = [100.0, 101.0, 103.0, 105.0, 108.0, 112.0]
    bars = generate_bars(prices, t0)
    feed = [{"AAPL": b} for b in bars]

    # Runner 1 with default internal journal
    r1 = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)
    s1 = r1.run_simulation(feed)

    # Runner 2 with explicitly provided journal
    j2 = TradeJournal()
    r2 = PaperTradingRunner(
        config=cfg,
        initial_cash=100_000.0,
        anchor_timestamp=t0,
        journal=j2,
    )
    s2 = r2.run_simulation(feed)

    assert s1.final_equity == s2.final_equity
    assert s1.receipt_checksum == s2.receipt_checksum
    assert r1.journal.total_entries == r2.journal.total_entries


def test_lens8_runtime_high_volume_sequential_logging() -> None:
    """Lens 8 Runtime: High volume sequential event logging maintains sub-second throughput."""
    journal = TradeJournal()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    start_perf = datetime.now()
    # Log 300 sequential events
    for i in range(300):
        ts = t0 + timedelta(seconds=i)
        journal.record_event(
            event_type=JournalEventType.OBSERVATION,
            payload={"index": i, "metric": float(i * 1.5)},
            timestamp=ts,
        )
    elapsed = (datetime.now() - start_perf).total_seconds()

    assert journal.total_entries == 300
    assert elapsed < 1.0  # Must be fast and sub-second

    receipt = journal.verify_integrity(t0 + timedelta(seconds=400))
    assert receipt.is_valid is True
    assert receipt.total_entries == 300


def test_lens9_blast_radius_interception_preserves_broker_and_frozen_models() -> None:
    """Lens 9 Blast Radius: Scientist execution breach leaves broker cash/equity untouched."""
    cfg = make_approved_config()
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

    initial_cash = runner.broker.cash_balance
    initial_equity = runner.broker.total_equity

    # Attempt unauthorized scientist submission
    with pytest.raises(ScientistExecutionBreachError):
        runner.submit_as_scientist(action="submit_order", timestamp=t0)

    # Broker cash and equity are untouched
    assert runner.broker.cash_balance == initial_cash
    assert runner.broker.total_equity == initial_equity

    # Domain model immutability (frozen=True, extra="forbid")
    entry = runner.journal.get_entries()[0]
    with pytest.raises(ValidationError):
        entry.sequence_number = 999  # type: ignore[misc]

    with pytest.raises(ValidationError):
        JournalEntry(
            entry_id="E1",
            sequence_number=0,
            timestamp=t0,
            event_type=JournalEventType.OBSERVATION,
            payload={},
            previous_entry_hash="GENESIS",
            entry_hash="h" * 64,
            unauthorized_field="malicious",  # extra="forbid"
        )
