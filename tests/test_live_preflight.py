"""Unit tests for TOSO Operational Pre-Flight Checks & Safety Engine (LIVE-003).

Verifies:
- AC-001: Clock synchronization within skew tolerance passes; excessive skew fails closed.
- AC-002: Broker session connectivity and authentication verified.
- AC-003: Zero resting/unrecognized open orders verified for clean-slate startup.
- AC-004: Account solvency and cash/equity reconciliation verified.
- AC-005: Circuit breakers and emergency kill-switch readiness verified.
- AC-006: Trade journal cryptographic append-only integrity verified.
- AC-007: Protection contract and universe mutual exclusivity verified.
- AC-008: End-to-end clean pre-flight receipt generated with SHA-256 seal.
- AC-009: raise_on_failure=False returns FAILED receipt without raising exception.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toso.config import load_config_from_file
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveUniverseConfig,
)
from toso.live import (
    BrokerConnectivityError,
    CheckStatus,
    CircuitBreakerCheckError,
    ClockSynchronizationError,
    JournalIntegrityCheckError,
    PreFlightCheckFailedError,
    PreFlightSafetyEngine,
    ReconciliationCheckError,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.journal import TradeJournal


@pytest.fixture
def canonical_config():
    return load_config_from_file(Path("config/core_config_v1.yaml"))


@pytest.fixture
def clean_runner(canonical_config):
    return PaperTradingRunner(config=canonical_config, initial_cash=100_000.0)


def test_full_clean_preflight_success(clean_runner):
    """AC-008: Complete pre-flight check sequence passes on clean runner."""
    fixed_time = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=fixed_time,
        reference_time=fixed_time,
    )

    assert receipt.overall_status == CheckStatus.PASSED
    assert receipt.all_passed is True
    assert len(receipt.checks) == 7
    assert all(c.status == CheckStatus.PASSED for c in receipt.checks)
    assert len(receipt.receipt_hash) == 64


def test_clock_skew_failure(clean_runner):
    """AC-001: Clock skew exceeding threshold fails closed."""
    t_local = datetime(2026, 11, 10, 12, 0, 10, tzinfo=UTC)
    t_ref = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)  # 10s skew > 5s max

    with pytest.raises(ClockSynchronizationError, match="Clock skew 10.000s exceeds"):
        PreFlightSafetyEngine.execute_preflight_checks(
            runner=clean_runner,
            current_time=t_local,
            reference_time=t_ref,
            max_clock_skew_seconds=5.0,
        )


def test_broker_missing_or_disconnected_failure(canonical_config):
    """AC-002: Missing or disconnected broker fails closed."""
    with pytest.raises(BrokerConnectivityError, match="Broker instance is None"):
        PreFlightSafetyEngine.execute_preflight_checks(
            broker=None, config=canonical_config, journal=TradeJournal()
        )

    mock_broker = MagicMock()
    mock_broker.is_connected = False
    with pytest.raises(BrokerConnectivityError, match="is_connected=False"):
        PreFlightSafetyEngine.execute_preflight_checks(
            broker=mock_broker, config=canonical_config, journal=TradeJournal()
        )


def test_resting_orders_clean_slate_failure(clean_runner):
    """AC-003: Resting open orders present on broker pre-flight fails closed."""
    # Mock a resting order in SUBMITTED state
    mock_order = MagicMock()
    mock_order.status = "SUBMITTED"
    clean_runner.broker._orders["ORD-RESTING-01"] = mock_order

    with pytest.raises(ReconciliationCheckError, match="active resting order"):
        PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)


def test_account_insolvency_or_nan_failure(clean_runner):
    """AC-004: Insolvent or NaN cash/equity fails closed."""
    mock_broker = MagicMock()
    mock_broker.is_connected = True
    mock_broker.account_id = "ACC-001"
    mock_broker.get_orders.return_value = {}
    mock_broker.cash_balance = float("nan")
    mock_broker.total_equity = 100_000.0

    with pytest.raises(ReconciliationCheckError, match="Broker cash balance is invalid"):
        PreFlightSafetyEngine.execute_preflight_checks(
            broker=mock_broker, config=clean_runner.config, journal=clean_runner.journal
        )


def test_halted_runner_circuit_breaker_failure(clean_runner):
    """AC-005: Already-halted runner fails circuit breaker readiness check."""
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    clean_runner.trigger_kill_switch(reason="Test Emergency Halt", timestamp=now)

    with pytest.raises(CircuitBreakerCheckError, match="Runner is currently in HALTED state"):
        PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner, current_time=now)


def test_journal_hash_compromised_failure(clean_runner):
    """AC-006: Tampered journal hash chain fails closed."""
    # Corrupt journal entries directly
    clean_runner.journal._entries[0] = clean_runner.journal._entries[0].model_copy(
        update={"entry_hash": "corrupted_hash_abc"}
    )

    with pytest.raises(JournalIntegrityCheckError, match="hash chain verification failed"):
        PreFlightSafetyEngine.execute_preflight_checks(runner=clean_runner)


def test_universe_collision_failure(clean_runner, canonical_config):
    """AC-007: Overlapping Core and Defensive universe fails closed."""
    colliding_defense = DefensiveUniverseConfig(
        approved_instruments=(
            DefensiveInstrumentSpec(
                symbol="AAPL",  # Overlaps with Core
                instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
                underlying_index="S&P 500",
                leverage_factor=-1.0,
                max_allocation_of_sleeve_pct=0.50,
            ),
        ),
        core_universe_symbols=canonical_config.universe.eligible_symbols,
    )
    with pytest.raises(PreFlightCheckFailedError, match="universe collision detected"):
        PreFlightSafetyEngine.execute_preflight_checks(
            runner=clean_runner, defense_universe=colliding_defense
        )


def test_raise_on_failure_false_returns_receipt(clean_runner):
    """AC-009: raise_on_failure=False returns receipt with FAILED status without raising."""
    t_local = datetime(2026, 11, 10, 12, 0, 10, tzinfo=UTC)
    t_ref = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=clean_runner,
        current_time=t_local,
        reference_time=t_ref,
        max_clock_skew_seconds=1.0,
        raise_on_failure=False,
    )
    assert receipt.overall_status == CheckStatus.FAILED
    assert receipt.all_passed is False
    assert any(c.status == CheckStatus.FAILED for c in receipt.checks)
