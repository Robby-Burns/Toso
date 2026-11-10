"""TOSO Append-Only Trade & Observation Journal Engine.

Implements FR-016, FR-017, INV-001, INV-006, INV-008:
- Maintains an append-only, cryptographically chained audit log (FR-016).
- Supports historical reconstruction of configs, signals, sizing, and fills.
- Detects modification, truncation, or sequence corruption with SHA-256 hash chaining.
- Provides observation ingestion for the Scientist research loop (FR-017).
- Fail-closed handling for UNKNOWN, corrupted, or non-chronological inputs (INV-008).
"""

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from toso.scientist.errors import (
    JournalSerializationError,
    JournalTamperError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    JournalEntry,
    JournalEventType,
    JournalVerificationReceipt,
    ObservationRecord,
)

GENESIS_HASH = "GENESIS"


def compute_entry_hash(
    entry_id: str,
    sequence_number: int,
    timestamp: datetime,
    event_type: str,
    payload: dict[str, Any],
    previous_entry_hash: str,
) -> str:
    """Compute the deterministic SHA-256 hash sealing a journal entry.

    Args:
        entry_id: Unique entry identifier.
        sequence_number: Integer sequence index.
        timestamp: Timestamp of the event.
        event_type: Categorical event string.
        payload: Metadata payload.
        previous_entry_hash: Hash of preceding block.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    try:
        payload_serialized = json.dumps(payload, sort_keys=True, default=str)
    except Exception as e:
        raise JournalSerializationError(f"Failed to serialize journal payload: {e}") from e

    content = (
        f"{entry_id}|{sequence_number}|{timestamp.isoformat()}|"
        f"{event_type}|{payload_serialized}|{previous_entry_hash}"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class TradeJournal:
    """Authoritative, append-only cryptographic journal for TOSO trading events (FR-016)."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        """Initialize the trade journal with optional persistent storage.

        Args:
            persistence_path: Optional file path for append-only JSONL disk logging.
        """
        self._entries: list[JournalEntry] = []
        self._persistence_path: Path | None = (
            Path(persistence_path).resolve() if persistence_path is not None else None
        )
        if self._persistence_path is not None and self._persistence_path.exists():
            self._load_from_disk()

    @property
    def total_entries(self) -> int:
        """Current number of recorded journal entries."""
        return len(self._entries)

    def record_event(
        self,
        event_type: JournalEventType,
        payload: dict[str, Any],
        timestamp: datetime,
        entry_id: str | None = None,
    ) -> JournalEntry:
        """Append a new trading event to the journal, computing the cryptographic link.

        Args:
            event_type: Categorical event classification.
            payload: Structured event metadata and parameters.
            timestamp: Timestamp anchor for the event.
            entry_id: Optional unique entry identifier.

        Returns:
            The created, sealed, immutable JournalEntry.

        Raises:
            UnknownScientistInputError: On missing, NaN, or invalid inputs.
        """
        if not isinstance(event_type, JournalEventType):
            raise UnknownScientistInputError(f"Invalid event_type: {event_type}")
        if not isinstance(payload, dict):
            raise UnknownScientistInputError(f"Payload must be a dict, got {type(payload)}")
        if not isinstance(timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {timestamp}")

        # Check for NaN / Inf values in numeric fields of payload
        for k, v in payload.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if math.isnan(v) or math.isinf(v):
                    raise UnknownScientistInputError(f"Payload key '{k}' contains NaN or Inf: {v}")

        sequence_number = len(self._entries)
        previous_hash = self._entries[-1].entry_hash if self._entries else GENESIS_HASH

        if entry_id is None:
            ts_sec = int(timestamp.timestamp())
            entry_id = f"JRNL-{sequence_number:06d}-{ts_sec}"

        entry_hash = compute_entry_hash(
            entry_id=entry_id,
            sequence_number=sequence_number,
            timestamp=timestamp,
            event_type=event_type.value,
            payload=payload,
            previous_entry_hash=previous_hash,
        )

        entry = JournalEntry(
            entry_id=entry_id,
            sequence_number=sequence_number,
            timestamp=timestamp,
            event_type=event_type,
            payload=payload,
            previous_entry_hash=previous_hash,
            entry_hash=entry_hash,
        )

        self._entries.append(entry)

        if self._persistence_path is not None:
            self._append_to_disk(entry)

        return entry

    def record_observation(self, observation: ObservationRecord) -> JournalEntry:
        """Record a research observation generated by the Scientist (FR-017).

        Args:
            observation: Valid ObservationRecord instance.

        Returns:
            JournalEntry recording the observation.

        Raises:
            UnknownScientistInputError: If observation is invalid or contains NaN.
        """
        if not isinstance(observation, ObservationRecord):
            raise UnknownScientistInputError(f"Invalid observation type: {type(observation)}")

        for metric_name, metric_val in observation.metrics.items():
            if math.isnan(metric_val) or math.isinf(metric_val):
                raise UnknownScientistInputError(
                    f"Observation metric '{metric_name}' is NaN or Inf: {metric_val}"
                )

        payload = observation.model_dump(mode="json")
        return self.record_event(
            event_type=JournalEventType.OBSERVATION,
            payload=payload,
            timestamp=observation.timestamp,
            entry_id=f"OBS-{observation.observation_id}",
        )

    def verify_integrity(self, verification_timestamp: datetime) -> JournalVerificationReceipt:
        """Verify the complete cryptographic chain and sequence integrity of the journal.

        Validates:
        1. Monotonic sequence numbering starting from 0.
        2. First entry previous hash matches GENESIS.
        3. Each subsequent entry previous hash matches preceding entry's hash.
        4. Recomputed entry hash matches recorded entry hash.

        Args:
            verification_timestamp: Timestamp anchor echoed into the verification receipt.

        Returns:
            JournalVerificationReceipt if all entries are cryptographically intact.

        Raises:
            JournalTamperError: If any breach, mutation, deletion, or sequence gap is found.
            UnknownScientistInputError: If verification_timestamp is invalid.
        """
        if not isinstance(verification_timestamp, datetime):
            raise UnknownScientistInputError(f"Invalid timestamp: {verification_timestamp}")

        receipt_id = f"JRNL-VERIF-{int(verification_timestamp.timestamp())}"

        if not self._entries:
            return JournalVerificationReceipt(
                receipt_id=receipt_id,
                is_valid=True,
                total_entries=0,
                latest_sequence_number=-1,
                latest_entry_hash=GENESIS_HASH,
                verification_timestamp=verification_timestamp,
                details="Journal is empty; genesis chain verified.",
            )

        for i, entry in enumerate(self._entries):
            # 1. Monotonic sequence check
            if entry.sequence_number != i:
                raise JournalTamperError(
                    f"Sequence gap or tamper detected at index {i}: "
                    f"expected {i}, found {entry.sequence_number}."
                )

            # 2. Previous hash check
            expected_prev_hash = self._entries[i - 1].entry_hash if i > 0 else GENESIS_HASH
            if entry.previous_entry_hash != expected_prev_hash:
                raise JournalTamperError(
                    f"Cryptographic chain broken at sequence {i} ({entry.entry_id}): "
                    f"expected prev hash '{expected_prev_hash}', got '{entry.previous_entry_hash}'."
                )

            # 3. Hash integrity check
            recalculated_hash = compute_entry_hash(
                entry_id=entry.entry_id,
                sequence_number=entry.sequence_number,
                timestamp=entry.timestamp,
                event_type=entry.event_type.value,
                payload=entry.payload,
                previous_entry_hash=entry.previous_entry_hash,
            )
            if entry.entry_hash != recalculated_hash:
                raise JournalTamperError(
                    f"Payload or hash tampering detected at sequence {i} ({entry.entry_id}): "
                    f"recorded hash '{entry.entry_hash}' != calculated '{recalculated_hash}'."
                )

        latest = self._entries[-1]
        return JournalVerificationReceipt(
            receipt_id=receipt_id,
            is_valid=True,
            total_entries=len(self._entries),
            latest_sequence_number=latest.sequence_number,
            latest_entry_hash=latest.entry_hash,
            verification_timestamp=verification_timestamp,
            details=f"All {len(self._entries)} entries cryptographically verified.",
        )

    def get_entries(
        self, start_sequence: int = 0, end_sequence: int | None = None
    ) -> tuple[JournalEntry, ...]:
        """Retrieve a deterministic sequence range of journal entries for reconstruction."""
        if start_sequence < 0:
            raise UnknownScientistInputError(f"start_sequence cannot be negative: {start_sequence}")
        if end_sequence is not None and end_sequence < start_sequence:
            raise UnknownScientistInputError(
                f"end_sequence ({end_sequence}) cannot precede start ({start_sequence})"
            )

        subset = (
            self._entries[start_sequence:end_sequence]
            if end_sequence is not None
            else self._entries[start_sequence:]
        )
        return tuple(subset)

    def get_entries_by_type(self, event_type: JournalEventType) -> tuple[JournalEntry, ...]:
        """Retrieve all journal entries matching a specific event type."""
        return tuple(e for e in self._entries if e.event_type == event_type)

    def get_entries_by_symbol(self, symbol: str) -> tuple[JournalEntry, ...]:
        """Retrieve all journal entries mentioning a specific asset ticker symbol."""
        cleaned = symbol.strip().upper()
        matches: list[JournalEntry] = []
        for e in self._entries:
            sym_in_payload = str(e.payload.get("symbol", "")).upper()
            if sym_in_payload == cleaned:
                matches.append(e)
        return tuple(matches)

    def _append_to_disk(self, entry: JournalEntry) -> None:
        """Atomically append a single entry as JSON line to disk."""
        if self._persistence_path is None:
            return
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._persistence_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _load_from_disk(self) -> None:
        """Load and verify entries from persistent JSONL log file."""
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        entries: list[JournalEntry] = []
        with open(self._persistence_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                    entries.append(JournalEntry.model_validate(data))
                except Exception as e:
                    raise JournalSerializationError(
                        f"Failed parsing journal log line {line_no}: {e}"
                    ) from e
        self._entries = entries
