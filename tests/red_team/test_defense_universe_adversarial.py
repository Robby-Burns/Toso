"""Adversarial (red‑team) tests for the Black Swan defensive universe validator.
These tests intentionally feed malformed, extreme, or ambiguous inputs to the
`enforce_defensive_instrument_eligibility` function to verify that the fail‑closed
behaviour and exception taxonomy are robust.
"""

import math
from datetime import UTC, datetime

import pytest

from toso.defense.errors import (
    CoreDefensiveUniverseCollisionError,
    DefensiveUniverseLiquidityError,
    DisallowedDefensiveInstrumentError,
    UnknownDefenseInputError,
)
from toso.defense.universe import (
    enforce_defensive_instrument_eligibility,
    load_canonical_defensive_universe,
)


# Helper to get a default config for tests
def _default_config():
    return load_canonical_defensive_universe()


@pytest.fixture
def config():
    return _default_config()


@pytest.fixture
def now():
    return datetime.now(UTC)


# ==== INPUT CORRUPTION ====


def test_invalid_symbol_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol="   ",  # whitespace only
            config=config,
            evaluation_timestamp=now,
        )


def test_none_symbol_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol=None,  # type: ignore[arg-type]
            config=config,
            evaluation_timestamp=now,
        )


# ==== NAN / INF VALUES ====


def test_nan_adv_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=math.nan,
            current_price=10.0,
        )


def test_inf_price_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=1_000_000,
            current_price=math.inf,
        )


# ==== NEGATIVE / ZERO VALUES ====


def test_negative_adv_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=-100,
            current_price=10.0,
        )


def test_zero_price_raises_unknown_input(config, now):
    with pytest.raises(UnknownDefenseInputError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=1_000_000,
            current_price=0.0,
        )


# ==== EXTREME BUT VALID VALUES (should still be eligible) ====


def test_extremely_large_adv_allows_eligibility(config, now):
    # Very large ADV that exceeds the minimum – should be eligible.
    receipt = enforce_defensive_instrument_eligibility(
        symbol="SH",
        config=config,
        evaluation_timestamp=now,
        current_average_daily_volume=10_000_000,
        current_price=25.0,
    )
    assert receipt.is_eligible
    assert receipt.status.name == "ELIGIBLE"


# ==== CORE COLLISION ====


def test_core_symbol_triggers_collision(config, now):
    # Use a known core symbol (AAPL) – expect CoreDefensiveUniverseCollisionError.
    with pytest.raises(CoreDefensiveUniverseCollisionError):
        enforce_defensive_instrument_eligibility(
            symbol="AAPL",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=1_000_000,
            current_price=150.0,
        )


# ==== UNAPPROVED SYMBOL ====


def test_unapproved_symbol_raises_disallowed_error(config, now):
    with pytest.raises(DisallowedDefensiveInstrumentError):
        enforce_defensive_instrument_eligibility(
            symbol="XYZ",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=1_000_000,
            current_price=10.0,
        )


# ==== LIQUIDITY FAILURE ====


def test_insufficient_adv_raises_liquidity_error(config, now):
    # SH requires 500 000 ADV; supply less to trigger error.
    with pytest.raises(DefensiveUniverseLiquidityError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=100_000,
            current_price=25.0,
        )


def test_price_below_minimum_raises_liquidity_error(config, now):
    # Minimum share price is $1.0; supply $0.5.
    with pytest.raises(DefensiveUniverseLiquidityError):
        enforce_defensive_instrument_eligibility(
            symbol="SH",
            config=config,
            evaluation_timestamp=now,
            current_average_daily_volume=1_000_000,
            current_price=0.5,
        )
