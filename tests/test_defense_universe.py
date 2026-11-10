"""Tests for Story BS-002: Black Swan Defensive Instruments & Universe Configuration.

Verifies:
- FR-010: Capital Separation and dedicated defensive asset universe.
- FR-011: Strict Core / Black Swan Independence (mutual exclusion between Core and defense).
- INV-013: Core / Black Swan Independence and sleeve boundaries.
- INV-008: Fail-closed gating on corrupted, unapproved, NaN, or invalid inputs.
- INV-001, INV-006: Pure determinism and immutable audit receipts.
- Spec Section 12.1: Defensive instrument taxonomy, leverage, and decay-limiting holding periods.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toso.defense import (
    CoreDefensiveUniverseCollisionError,
    DefensiveEligibilityStatus,
    DefensiveInstrumentType,
    DefensiveUniverseConfig,
    DefensiveUniverseLiquidityError,
    DisallowedDefensiveInstrumentError,
    UnknownDefenseInputError,
    enforce_defensive_instrument_eligibility,
    evaluate_defensive_instrument_eligibility,
    load_canonical_defensive_universe,
)


@pytest.fixture
def test_timestamp() -> datetime:
    return datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC)


@pytest.fixture
def canonical_universe() -> DefensiveUniverseConfig:
    return load_canonical_defensive_universe()


def test_ac001_approved_defensive_instruments_eligible(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """AC-001: Canonical approved instruments are verified eligible and accurately classified."""
    expected_instruments = {
        "SH": DefensiveInstrumentType.INVERSE_INDEX_ETF,
        "PSQ": DefensiveInstrumentType.INVERSE_INDEX_ETF,
        "SPXU": DefensiveInstrumentType.LEVERAGED_INVERSE_ETF,
        "VIXY": DefensiveInstrumentType.VOLATILITY_ETP,
        "BIL": DefensiveInstrumentType.CASH_EQUIVALENT,
    }

    for symbol, expected_type in expected_instruments.items():
        receipt = evaluate_defensive_instrument_eligibility(
            symbol=symbol,
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
        )
        assert receipt.is_eligible is True
        assert receipt.status == DefensiveEligibilityStatus.ELIGIBLE
        assert receipt.instrument_spec is not None
        assert receipt.instrument_spec.instrument_type == expected_type
        assert receipt.symbol == symbol

        enforced_receipt = enforce_defensive_instrument_eligibility(
            symbol=symbol,
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
        )
        assert enforced_receipt.is_eligible is True


def test_ac002_unapproved_symbol_rejected(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """AC-002: Unapproved tickers fail eligibility and raise DisallowedDefensiveInstrumentError."""
    unapproved_symbols = ["XYZ", "TSLA", "GLD", "UNKNOWN_ETF"]

    for symbol in unapproved_symbols:
        receipt = evaluate_defensive_instrument_eligibility(
            symbol=symbol,
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
        )
        assert receipt.is_eligible is False
        assert receipt.status == DefensiveEligibilityStatus.UNAPPROVED_SYMBOL
        assert receipt.instrument_spec is None
        assert "not an approved Black Swan defensive instrument" in receipt.reasons[0]

        with pytest.raises(DisallowedDefensiveInstrumentError) as exc_info:
            enforce_defensive_instrument_eligibility(
                symbol=symbol,
                config=canonical_universe,
                evaluation_timestamp=test_timestamp,
            )
        assert "not an approved" in str(exc_info.value)


def test_ac003_core_equity_collision_rejected(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """AC-003: Core equities submitted to defense raise CoreDefensiveUniverseCollisionError."""
    core_equities = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]

    for symbol in core_equities:
        receipt = evaluate_defensive_instrument_eligibility(
            symbol=symbol,
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
        )
        assert receipt.is_eligible is False
        assert receipt.status == DefensiveEligibilityStatus.UNIVERSE_COLLISION
        assert receipt.instrument_spec is None
        assert "belongs to the Core trading universe" in receipt.reasons[0]

        with pytest.raises(CoreDefensiveUniverseCollisionError) as exc_info:
            enforce_defensive_instrument_eligibility(
                symbol=symbol,
                config=canonical_universe,
                evaluation_timestamp=test_timestamp,
            )
        assert "belongs to the Core trading universe" in str(exc_info.value)


def test_ac004_insufficient_liquidity_rejected(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """AC-004: Defensive instruments failing ADV or price liquidity thresholds are blocked."""
    # ADV below minimum for SH (min is 500,000)
    receipt_adv = evaluate_defensive_instrument_eligibility(
        symbol="SH",
        config=canonical_universe,
        evaluation_timestamp=test_timestamp,
        current_average_daily_volume=10000,
        current_price=20.0,
    )
    assert receipt_adv.is_eligible is False
    assert receipt_adv.status == DefensiveEligibilityStatus.INSUFFICIENT_LIQUIDITY
    assert "below instrument minimum" in receipt_adv.reasons[0]

    with pytest.raises(DefensiveUniverseLiquidityError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
            current_average_daily_volume=10000,
            current_price=20.0,
        )

    # Share price below minimum threshold (1.0)
    receipt_price = evaluate_defensive_instrument_eligibility(
        symbol="SH",
        config=canonical_universe,
        evaluation_timestamp=test_timestamp,
        current_average_daily_volume=600000,
        current_price=0.50,
    )
    assert receipt_price.is_eligible is False
    assert receipt_price.status == DefensiveEligibilityStatus.INSUFFICIENT_LIQUIDITY
    assert "below minimum share price" in receipt_price.reasons[0]

    with pytest.raises(DefensiveUniverseLiquidityError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=canonical_universe,
            evaluation_timestamp=test_timestamp,
            current_average_daily_volume=600000,
            current_price=0.50,
        )


def test_ac005_leverage_and_decay_holding_limits_configured(
    canonical_universe: DefensiveUniverseConfig,
) -> None:
    """AC-005: Instruments enforce leverage and holding periods to prevent decay (RISK-005)."""
    spec_map = {s.symbol: s for s in canonical_universe.approved_instruments}

    # SPXU is 3x leveraged inverse; holding period must be bounded to 20 days
    spxu = spec_map["SPXU"]
    assert spxu.leverage_factor == -3.0
    assert spxu.max_holding_period_days == 20
    assert spxu.max_allocation_of_sleeve_pct == 0.25

    # VIXY is volatility ETP; holding period bounded to 15 days due to roll decay
    vixy = spec_map["VIXY"]
    assert vixy.leverage_factor == 1.0
    assert vixy.max_holding_period_days == 15
    assert vixy.max_allocation_of_sleeve_pct == 0.25

    # BIL is cash equivalent; no holding period limit (indefinite preservation)
    bil = spec_map["BIL"]
    assert bil.leverage_factor == 1.0
    assert bil.max_holding_period_days is None
    assert bil.max_allocation_of_sleeve_pct == 1.00


def test_fail_closed_on_corrupt_unknown_inputs(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """INV-008: Fail-closed on missing, whitespace, non-positive, or NaN inputs."""
    # Invalid symbol types or empty strings
    for bad_symbol in ["", "   ", None, 123, []]:
        with pytest.raises(UnknownDefenseInputError):
            evaluate_defensive_instrument_eligibility(
                symbol=bad_symbol,  # type: ignore[arg-type]
                config=canonical_universe,
                evaluation_timestamp=test_timestamp,
            )

    # Invalid timestamp
    with pytest.raises(UnknownDefenseInputError):
        evaluate_defensive_instrument_eligibility(
            symbol="SH",
            config=canonical_universe,
            evaluation_timestamp=None,  # type: ignore[arg-type]
        )

    # Invalid config
    with pytest.raises(UnknownDefenseInputError):
        evaluate_defensive_instrument_eligibility(
            symbol="SH",
            config="not_a_config",  # type: ignore[arg-type]
            evaluation_timestamp=test_timestamp,
        )

    # NaN / Inf / Negative volume
    for bad_vol in [float("nan"), float("inf"), -100, "1000"]:
        with pytest.raises(UnknownDefenseInputError):
            evaluate_defensive_instrument_eligibility(
                symbol="SH",
                config=canonical_universe,
                evaluation_timestamp=test_timestamp,
                current_average_daily_volume=bad_vol,  # type: ignore[arg-type]
            )

    # NaN / Inf / Zero / Negative price
    for bad_price in [float("nan"), float("inf"), 0.0, -5.0, "20"]:
        with pytest.raises(UnknownDefenseInputError):
            evaluate_defensive_instrument_eligibility(
                symbol="SH",
                config=canonical_universe,
                evaluation_timestamp=test_timestamp,
                current_price=bad_price,  # type: ignore[arg-type]
            )


def test_pure_determinism_and_immutable_receipts(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """INV-001, INV-006: Receipts are strictly reproducible and immutable."""
    receipt1 = evaluate_defensive_instrument_eligibility(
        symbol="SH",
        config=canonical_universe,
        evaluation_timestamp=test_timestamp,
        current_average_daily_volume=600000,
        current_price=15.0,
    )
    receipt2 = evaluate_defensive_instrument_eligibility(
        symbol="SH",
        config=canonical_universe,
        evaluation_timestamp=test_timestamp,
        current_average_daily_volume=600000,
        current_price=15.0,
    )

    assert receipt1 == receipt2
    assert receipt1.evaluation_timestamp == test_timestamp

    # Immutability verification
    with pytest.raises(ValidationError):
        receipt1.is_eligible = False  # type: ignore[misc]

    with pytest.raises(ValidationError):
        canonical_universe.min_share_price_dollars = 5.0  # type: ignore[misc]


def test_case_insensitive_symbol_handling(
    canonical_universe: DefensiveUniverseConfig, test_timestamp: datetime
) -> None:
    """Case normalization: symbols evaluate correctly regardless of input case."""
    receipt_lower = evaluate_defensive_instrument_eligibility(
        symbol="spxu",
        config=canonical_universe,
        evaluation_timestamp=test_timestamp,
    )
    assert receipt_lower.is_eligible is True
    assert receipt_lower.symbol == "SPXU"
    assert receipt_lower.status == DefensiveEligibilityStatus.ELIGIBLE
