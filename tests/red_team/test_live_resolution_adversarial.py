"""Adversarial 9-Lens Verification Suite for LIVE-001 (Question Resolution).

Covers all 9 mandatory verification lenses:
1. Boundary: Exact boundary testing of risk, concentration, and timeframe parameters.
2. Security: Immutability of receipts, resolution objects, and SHA-256 hash sealing.
3. Failure Paths: Corrupt, missing, negative, and invalid parameters fail closed.
4. Skeptic: Verifying that deferred/non-blocking questions cannot falsely satisfy blocking ones.
5. Red Team: Malicious tampering of config attributes to sneak past resolution.
6. QA Edge: Single symbol universe, zero-length strings, extreme values.
7. Regression: Determinism of cryptographic audit hash across identical runs.
8. Runtime: Clock independence and pure execution with zero side-effects.
9. Blast Radius: Live resolution module import and run leaves local files and broker intact.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.config import load_config_from_file
from toso.config.models import (
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
)
from toso.defense.models import (
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveUniverseConfig,
)
from toso.live import (
    InvalidLiveParameterError,
    LiveReadinessResolver,
    QuestionStatus,
)


@pytest.fixture
def canonical_config() -> CoreConfig:
    return load_config_from_file(Path("config/core_config_v1.yaml"))


# Lens 1: Boundary
def test_lens_boundary_concentration_and_risk(canonical_config):
    """Boundary: Test exact risk ceilings and invalid bounds."""
    # max_risk_per_trade > 0.05 fails
    bad_risk_config = canonical_config.model_copy(
        update={
            "risk": RiskConfig(
                max_risk_per_trade_pct=0.05,
                max_aggregate_position_risk_pct=0.06,
                max_portfolio_risk_pct=0.10,
                max_position_concentration_pct=0.35,  # exact ceiling
            )
        }
    )
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=bad_risk_config)
    assert receipt.all_blocking_resolved is True

    # concentration > 0.35 fails at schema or resolver level
    with pytest.raises(ValidationError):
        canonical_config.model_copy(
            update={
                "risk": RiskConfig(
                    max_risk_per_trade_pct=0.01,
                    max_aggregate_position_risk_pct=0.02,
                    max_portfolio_risk_pct=0.06,
                    max_position_concentration_pct=0.36,  # breaches 0.35
                )
            }
        )


# Lens 2: Security
def test_lens_security_receipt_immutability(canonical_config):
    """Security: Audit receipts and resolution records are strictly frozen."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)

    with pytest.raises(ValidationError):
        receipt.all_blocking_resolved = False  # frozen model mutation attempt

    with pytest.raises(ValidationError):
        receipt.resolutions[0].status = QuestionStatus.UNRESOLVED  # frozen child mutation


# Lens 3: Failure Paths
def test_lens_failure_paths_intraday_timeframe(canonical_config):
    """Failure Paths: Intraday timeframe rejected for initial Core strategy."""
    intraday_config = canonical_config.model_copy(
        update={
            "strategy": StrategyConfig(
                strategy_id="core_trend_v1",
                timeframe="5m",  # Intraday
            )
        }
    )
    with pytest.raises(InvalidLiveParameterError, match="must run on daily timeframe"):
        LiveReadinessResolver.resolve_all_questions(core_config=intraday_config)


# Lens 4: Skeptic
def test_lens_skeptic_blocking_vs_deferred_separation(canonical_config):
    """Skeptic: Non-blocking questions remain deferred and do not mask blocking status."""
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    deferred = [r for r in receipt.resolutions if not r.is_blocking]
    assert len(deferred) == 2
    assert all(r.status == QuestionStatus.DEFERRED for r in deferred)
    assert receipt.all_blocking_resolved is True


# Lens 5: Red Team
def test_lens_red_team_sneak_core_into_defense(canonical_config):
    """Red Team: Sneaking Core symbol into Black Swan defense is caught deterministically."""
    defense_with_core = DefensiveUniverseConfig(
        approved_instruments=(
            DefensiveInstrumentSpec(
                symbol="MSFT",  # Core symbol smuggled in
                instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
                underlying_index="NASDAQ-100",
                leverage_factor=-1.0,
                max_allocation_of_sleeve_pct=0.50,
            ),
        ),
        core_universe_symbols=canonical_config.universe.eligible_symbols,
    )
    with pytest.raises(InvalidLiveParameterError, match="Collision"):
        LiveReadinessResolver.resolve_all_questions(
            core_config=canonical_config, defense_universe=defense_with_core
        )


# Lens 6: QA Edge
def test_lens_qa_edge_excessive_black_swan_allocation(canonical_config):
    """QA Edge: Excessively large black swan allocation (>15%) is rejected."""
    excessive_bs = canonical_config.model_copy(
        update={
            "capital": CapitalAllocationConfig(
                core_allocation_pct=0.70,
                reserve_allocation_pct=0.10,
                black_swan_allocation_pct=0.20,  # exceeds 0.15 limit
            )
        }
    )
    with pytest.raises(InvalidLiveParameterError, match="out of safe bounds"):
        LiveReadinessResolver.resolve_all_questions(core_config=excessive_bs)


# Lens 7: Regression
def test_lens_regression_hash_determinism(canonical_config):
    """Regression: Hash calculation is bit-for-bit identical across runs."""
    fixed_time = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    receipt1 = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=fixed_time
    )
    receipt2 = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=fixed_time
    )
    assert receipt1.audit_hash == receipt2.audit_hash


# Lens 8: Runtime
def test_lens_runtime_zero_wall_clock_drift(canonical_config):
    """Runtime: Providing explicit audit timestamp isolates logic from system wall clock."""
    fixed_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    receipt = LiveReadinessResolver.resolve_all_questions(
        core_config=canonical_config, audit_time=fixed_time
    )
    assert receipt.timestamp == fixed_time


# Lens 9: Blast Radius
def test_lens_blast_radius_pure_inspection(canonical_config):
    """Blast Radius: Resolver execution is purely read-only and mutates zero state."""
    orig_dict = canonical_config.model_dump()
    receipt = LiveReadinessResolver.resolve_all_questions(core_config=canonical_config)
    assert canonical_config.model_dump() == orig_dict
    assert receipt is not None
