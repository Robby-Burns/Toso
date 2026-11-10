"""Tests for Story SCI-001: Observation & Append-Only Trade Journaling.

Verifies:
- FR-016: Append-only trade journaling supporting full historical reconstruction.
- FR-017: Scientist research observation ingestion.
- INV-014: Observation separation without execution authority.
- INV-008: Fail-closed handling on corrupt, missing, or NaN inputs.
- INV-001, INV-006: Pure determinism and cryptographic audit receipts.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.scientist import (
    JournalEntry,
    JournalEventType,
    JournalTamperError,
    ObservationRecord,
    TradeJournal,
    UnknownScientistInputError,
)


@pytest.fixture
def test_timestamp() -> datetime:
    return datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC)


def test_ac001_append_only_event_logging(test_timestamp: datetime) -> None:
    """AC-001: Records trading events across all mandatory categories (FR-016)."""
    journal = TradeJournal()

    events_to_record = [
        (JournalEventType.CONFIGURATION_LOADED, {"version": "1.0.0", "strategy": "core_trend"}),
        (JournalEventType.SIGNAL_GENERATED, {"symbol": "AAPL", "signal": "BUY"}),
        (JournalEventType.SIZING_CALCULATED, {"symbol": "AAPL", "shares": 100}),
        (JournalEventType.RISK_EVALUATED, {"symbol": "AAPL", "approved": True}),
        (JournalEventType.EXECUTION_SUBMITTED, {"order_id": "ORD-001", "symbol": "AAPL"}),
        (JournalEventType.BROKER_FILL, {"order_id": "ORD-001", "fill_price": 150.0}),
        (JournalEventType.POSITION_CHANGED, {"symbol": "AAPL", "new_shares": 100}),
        (JournalEventType.INVALIDATION_TRIGGERED, {"symbol": "AAPL", "stop_price": 140.0}),
        (JournalEventType.PYRAMID_ADDED, {"symbol": "AAPL", "add_shares": 50}),
        (JournalEventType.DEFENSE_TRIGGERED, {"trigger": "VOLATILITY_SPIKE", "symbol": "SH"}),
        (JournalEventType.FAILURE_RECORDED, {"error": "BrokerTimeout"}),
        (JournalEventType.EMERGENCY_STOP, {"reason": "DrawdownLimit"}),
    ]

    for event_type, payload in events_to_record:
        entry = journal.record_event(
            event_type=event_type,
            payload=payload,
            timestamp=test_timestamp,
        )
        assert entry.event_type == event_type
        assert entry.payload == payload

    assert journal.total_entries == len(events_to_record)

    # Verify cryptographic integrity
    receipt = journal.verify_integrity(verification_timestamp=test_timestamp)
    assert receipt.is_valid is True
    assert receipt.total_entries == len(events_to_record)
    assert receipt.latest_sequence_number == len(events_to_record) - 1


def test_ac002_cryptographic_chain_integrity(test_timestamp: datetime) -> None:
    """AC-002: Entries are linked via SHA-256 hash chaining starting from GENESIS."""
    journal = TradeJournal()

    e0 = journal.record_event(
        event_type=JournalEventType.SIGNAL_GENERATED,
        payload={"symbol": "NVDA", "action": "BUY"},
        timestamp=test_timestamp,
    )
    assert e0.sequence_number == 0
    assert e0.previous_entry_hash == "GENESIS"
    assert len(e0.entry_hash) == 64  # SHA-256 hex digest

    e1 = journal.record_event(
        event_type=JournalEventType.SIZING_CALCULATED,
        payload={"symbol": "NVDA", "shares": 50},
        timestamp=test_timestamp,
    )
    assert e1.sequence_number == 1
    assert e1.previous_entry_hash == e0.entry_hash
    assert e1.entry_hash != e0.entry_hash

    receipt = journal.verify_integrity(verification_timestamp=test_timestamp)
    assert receipt.is_valid is True
    assert receipt.latest_entry_hash == e1.entry_hash


def test_ac003_tamper_detection_on_mutation_or_deletion(test_timestamp: datetime) -> None:
    """AC-003: Modifying, replacing, or deleting any entry raises JournalTamperError."""
    journal = TradeJournal()

    journal.record_event(
        event_type=JournalEventType.SIGNAL_GENERATED,
        payload={"symbol": "MSFT"},
        timestamp=test_timestamp,
    )
    journal.record_event(
        event_type=JournalEventType.SIZING_CALCULATED,
        payload={"symbol": "MSFT", "shares": 75},
        timestamp=test_timestamp,
    )
    journal.record_event(
        event_type=JournalEventType.EXECUTION_SUBMITTED,
        payload={"symbol": "MSFT", "order_id": "ORD-1"},
        timestamp=test_timestamp,
    )

    # Verify baseline is intact
    assert journal.verify_integrity(test_timestamp).is_valid is True

    # 1. Tamper: Mutate payload in an existing entry
    tampered_entry = JournalEntry(
        entry_id=journal._entries[1].entry_id,
        sequence_number=journal._entries[1].sequence_number,
        timestamp=journal._entries[1].timestamp,
        event_type=journal._entries[1].event_type,
        payload={"symbol": "MSFT", "shares": 9999},  # Altered shares!
        previous_entry_hash=journal._entries[1].previous_entry_hash,
        entry_hash=journal._entries[1].entry_hash,
    )
    journal._entries[1] = tampered_entry

    with pytest.raises(JournalTamperError) as exc_info:
        journal.verify_integrity(test_timestamp)
    assert "tampering detected" in str(exc_info.value)

    # 2. Tamper: Delete an entry in the middle
    del journal._entries[1]
    with pytest.raises(JournalTamperError) as exc_info:
        journal.verify_integrity(test_timestamp)
    assert "gap" in str(exc_info.value) or "broken" in str(exc_info.value)


def test_ac004_observation_recording(test_timestamp: datetime) -> None:
    """AC-004: Ingests structured Scientist research observations (FR-017)."""
    journal = TradeJournal()

    obs = ObservationRecord(
        observation_id="OBS-20260914-01",
        timestamp=test_timestamp,
        topic="SlippageAnomalies",
        symbol="AAPL",
        metrics={"average_slippage_bps": 12.5, "fill_rate": 0.98},
        notes="Observed higher slippage at open auctions; recommends study in SCI-004.",
    )

    entry = journal.record_observation(obs)
    assert entry.event_type == JournalEventType.OBSERVATION
    assert entry.payload["topic"] == "SlippageAnomalies"
    assert entry.payload["metrics"]["average_slippage_bps"] == 12.5

    receipt = journal.verify_integrity(test_timestamp)
    assert receipt.is_valid is True


def test_ac005_historical_reconstruction_queries(test_timestamp: datetime) -> None:
    """AC-005: Supports range queries, type filtering, and symbol lookups for reconstruction."""
    journal = TradeJournal()

    journal.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "AAPL"}, test_timestamp)
    journal.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "MSFT"}, test_timestamp)
    journal.record_event(JournalEventType.BROKER_FILL, {"symbol": "AAPL"}, test_timestamp)
    journal.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "NVDA"}, test_timestamp)

    # Query range
    range_entries = journal.get_entries(start_sequence=1, end_sequence=3)
    assert len(range_entries) == 2
    assert range_entries[0].sequence_number == 1
    assert range_entries[1].sequence_number == 2

    # Filter by event type
    signal_entries = journal.get_entries_by_type(JournalEventType.SIGNAL_GENERATED)
    assert len(signal_entries) == 3

    # Filter by symbol
    aapl_entries = journal.get_entries_by_symbol("aapl")
    assert len(aapl_entries) == 2


def test_ac006_persistent_disk_logging(tmp_path: Path, test_timestamp: datetime) -> None:
    """AC-006: Logs append-only to JSONL on disk and recovers with integrity verified."""
    log_file = tmp_path / "trade_journal.jsonl"
    journal1 = TradeJournal(persistence_path=log_file)

    journal1.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "GOOGL"}, test_timestamp)
    journal1.record_event(JournalEventType.SIZING_CALCULATED, {"symbol": "GOOGL"}, test_timestamp)

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    # New journal instance loaded from same file
    journal2 = TradeJournal(persistence_path=log_file)
    assert journal2.total_entries == 2
    receipt = journal2.verify_integrity(test_timestamp)
    assert receipt.is_valid is True


def test_fail_closed_on_corrupt_unknown_inputs(test_timestamp: datetime) -> None:
    """INV-008: Fail-closed on missing types, NaN metrics, or invalid payloads."""
    journal = TradeJournal()

    # Invalid event type
    with pytest.raises(UnknownScientistInputError):
        journal.record_event(
            event_type="NOT_AN_EVENT",  # type: ignore[arg-type]
            payload={},
            timestamp=test_timestamp,
        )

    # Invalid payload type
    with pytest.raises(UnknownScientistInputError):
        journal.record_event(
            event_type=JournalEventType.SIGNAL_GENERATED,
            payload="not_a_dict",  # type: ignore[arg-type]
            timestamp=test_timestamp,
        )

    # NaN in payload
    with pytest.raises(UnknownScientistInputError):
        journal.record_event(
            event_type=JournalEventType.SIZING_CALCULATED,
            payload={"shares": float("nan")},
            timestamp=test_timestamp,
        )

    # NaN in observation metrics
    bad_obs = ObservationRecord(
        observation_id="OBS-BAD-01",
        timestamp=test_timestamp,
        topic="Anomaly",
        metrics={"bad_val": float("nan")},
    )
    with pytest.raises(UnknownScientistInputError):
        journal.record_observation(bad_obs)


def test_pure_determinism_and_immutability(test_timestamp: datetime) -> None:
    """INV-001, INV-006: Identical event sequences produce identical hashes; models frozen."""
    j1 = TradeJournal()
    j2 = TradeJournal()

    e1 = j1.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "AAPL"}, test_timestamp)
    e2 = j2.record_event(JournalEventType.SIGNAL_GENERATED, {"symbol": "AAPL"}, test_timestamp)

    assert e1.entry_hash == e2.entry_hash
    assert e1.timestamp == e2.timestamp

    with pytest.raises(ValidationError):
        e1.payload = {"tampered": True}  # type: ignore[misc]
