"""Unit tests for TOSO Live Readiness Question Resolution & Parameter Locking (LIVE-001).

Verifies:
- AC-001: Core risk parameters are resolved, bounded, and immutable.
- AC-002: Concentration thresholds are locked within safety ceiling.
- AC-003: Signal frequency is locked to daily (1d) timeframe.
- AC-004: Deterministic winner and strength conditions are locked.
- AC-005: Pyramiding spacing (>= 1.0R) and max adds are locked.
- AC-006: Black swan defensive instruments are approved and disjoint from Core.
- AC-007: Black swan capital allocation is bounded (5%).
- AC-008: Short-sale borrow rules are explicitly resolved.
- AC-009: Universe eligibility rules are resolved.
- AC-010: Cryptographic audit receipt generated with SHA-256 hash.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from toso.config import load_config_from_file
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveUniverseConfig,
)
from toso.live import (
    InvalidLiveParameterError,
    LiveReadinessResolver,
    QuestionStatus,
    UnresolvedLiveQuestionError,
)


@pytest.fixture
def canonical_config():
    """Load canonical approved Core configuration."""
    config_path = Path("config/core_config_v1.yaml")
    return load_config_from_file(config_path)


def test_canonical_resolution_success(canonical_config):
    """AC-001 to AC-010: Canonical configuration resolves all blocking questions."""
    fixed_time = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    receipt = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=fixed_time
    )

    assert receipt.all_blocking_resolved is True
    assert receipt.config_version == "1.0.0"
    assert len(receipt.resolutions) == 11

    # Verify all blocking are RESOLVED
    blocking = [r for r in receipt.resolutions if r.is_blocking]
    assert len(blocking) == 9
    assert all(r.status == QuestionStatus.RESOLVED for r in blocking)

    # Verify SHA-256 hash is non-empty hex string
    assert len(receipt.audit_hash) == 64
    assert all(c in "0123456789abcdef" for c in receipt.audit_hash)


def test_ac_001_core_risk_parameters_audit(canonical_config):
    """AC-001: Core risk parameters are accurately captured."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q01 = next(r for r in receipt.resolutions if r.question_id == "Q-01")
    assert q01.locked_value["max_risk_per_trade_pct"] == 0.01
    assert q01.locked_value["max_aggregate_position_risk_pct"] == 0.02
    assert q01.locked_value["max_portfolio_risk_pct"] == 0.06
    assert q01.locked_value["slippage_buffer_pct"] == 0.005


def test_ac_002_concentration_threshold_audit(canonical_config):
    """AC-002: Concentration threshold is locked at 20%."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q02 = next(r for r in receipt.resolutions if r.question_id == "Q-02")
    assert q02.locked_value == 0.20


def test_ac_003_timeframe_enforcement(canonical_config):
    """AC-003: Timeframe is locked to daily (1d)."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q03 = next(r for r in receipt.resolutions if r.question_id == "Q-03")
    assert q03.locked_value == "1d"


def test_ac_004_deterministic_winner_rules(canonical_config):
    """AC-004: Deterministic winner and trend conditions."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q04 = next(r for r in receipt.resolutions if r.question_id == "Q-04")
    assert q04.locked_value["breakout_lookback_bars"] == 20
    assert q04.locked_value["fast_ma_period"] == 50
    assert q04.locked_value["slow_ma_period"] == 200
    assert q04.locked_value["invalidation_atr_multiple"] == 2.0


def test_ac_005_pyramiding_spacing(canonical_config):
    """AC-005: Pyramiding minimum profit spacing and max additions."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q05 = next(r for r in receipt.resolutions if r.question_id == "Q-05")
    assert q05.locked_value["pyramid_min_profit_r"] == 1.0
    assert q05.locked_value["max_pyramid_adds"] == 3


def test_ac_006_defense_universe_and_collision_rejection(canonical_config):
    """AC-006: Black swan defense universe is disjoint and approved."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    assert set(receipt.defensive_symbols) == {"SH", "PSQ", "SPXU", "VIXY", "BIL"}

    # Injected collision must fail closed
    colliding_defense = DefensiveUniverseConfig(
        approved_instruments=(
            DefensiveInstrumentSpec(
                symbol="AAPL",  # Collides with Core symbol
                instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
                underlying_index="S&P 500",
                leverage_factor=-1.0,
                max_allocation_of_sleeve_pct=0.50,
            ),
        ),
        core_universe_symbols=canonical_config.universe.eligible_symbols,
    )
    with pytest.raises(
        InvalidLiveParameterError, match="Core and Defensive universes must be strictly disjoint"
    ):
        LiveReadinessResolver.resolve_all_questions(
            core_config=canonical_config, defense_universe=colliding_defense
        )


def test_ac_007_black_swan_allocation(canonical_config):
    """AC-007: Black Swan allocation is locked at 5%."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q07 = next(r for r in receipt.resolutions if r.question_id == "Q-07")
    assert q07.locked_value == 0.05


def test_ac_008_short_selling_disabled(canonical_config):
    """AC-008: Short selling is explicitly False."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q08 = next(r for r in receipt.resolutions if r.question_id == "Q-08")
    assert q08.locked_value is False


def test_ac_009_universe_eligibility(canonical_config):
    """AC-009: Universe symbols, ADV, and min price."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    q09 = next(r for r in receipt.resolutions if r.question_id == "Q-09")
    assert q09.locked_value["eligible_symbols"] == ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
    assert q09.locked_value["min_average_daily_volume_shares"] == 500000
    assert q09.locked_value["min_share_price_dollars"] == 5.0


def test_ac_010_empty_defensive_universe_rejection(canonical_config):
    """AC-010: Empty defensive universe fails closed."""
    empty_defense = DefensiveUniverseConfig(
        approved_instruments=(),
        core_universe_symbols=canonical_config.universe.eligible_symbols,
    )
    with pytest.raises(
        UnresolvedLiveQuestionError, match="No approved Black Swan defensive instruments"
    ):
        LiveReadinessResolver.resolve_all_questions(
            core_config=canonical_config, defense_universe=empty_defense
        )
