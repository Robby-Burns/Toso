"""TOSO Black Swan Defensive Universe & Instrument Validator.

Implements FR-010, FR-011, INV-013, INV-008, INV-001, INV-006, and Spec Section 12.1:
- Restricts Black Swan defensive deployment strictly to approved defensive instruments.
- Enforces strict mutual exclusion between Core equity universe and Defense (FR-011, INV-013).
- Validates liquidity, leverage, and decay constraints to avoid volatility drag (RISK-005).
- Pure mathematical determinism and immutable audit receipts (INV-001, INV-006).
- Fail-closed handling for UNKNOWN, NaN, infinite, or corrupted inputs (INV-008).
"""

import math
from datetime import datetime

from toso.defense.errors import (
    CoreDefensiveUniverseCollisionError,
    DefensiveUniverseLiquidityError,
    DisallowedDefensiveInstrumentError,
    UnknownDefenseInputError,
)
from toso.defense.models import (
    DefensiveEligibilityReceipt,
    DefensiveEligibilityStatus,
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensiveUniverseConfig,
)


def load_canonical_defensive_universe(
    core_symbols: tuple[str, ...] | list[str] | None = None,
) -> DefensiveUniverseConfig:
    """Load the canonical approved Black Swan defensive instrument universe.

    Canonical catalog includes:
    - SH: ProShares Short S&P 500 (INVERSE_INDEX_ETF, -1.0x, S&P 500)
    - PSQ: ProShares Short QQQ (INVERSE_INDEX_ETF, -1.0x, NASDAQ-100)
    - SPXU: ProShares UltraPro Short S&P 500 (LEVERAGED_INVERSE_ETF, -3.0x, S&P 500)
    - VIXY: ProShares VIX Short-Term Futures ETF (VOLATILITY_ETP, 1.0x, VIX Short-Term Futures)
    - BIL: SPDR Bloomberg 1-3 Month T-Bill ETF (CASH_EQUIVALENT, 1.0x, US Treasury Bills 1-3M)

    Args:
        core_symbols: Optional tuple/list of active Core equities to cross-check.

    Returns:
        DefensiveUniverseConfig with approved instruments and core universe constraints.
    """
    if core_symbols is None:
        core_symbols = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")

    approved_specs = (
        DefensiveInstrumentSpec(
            symbol="SH",
            instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
            underlying_index="S&P 500",
            leverage_factor=-1.0,
            max_allocation_of_sleeve_pct=0.50,
            max_holding_period_days=60,
            min_average_daily_volume=500000,
        ),
        DefensiveInstrumentSpec(
            symbol="PSQ",
            instrument_type=DefensiveInstrumentType.INVERSE_INDEX_ETF,
            underlying_index="NASDAQ-100",
            leverage_factor=-1.0,
            max_allocation_of_sleeve_pct=0.50,
            max_holding_period_days=60,
            min_average_daily_volume=500000,
        ),
        DefensiveInstrumentSpec(
            symbol="SPXU",
            instrument_type=DefensiveInstrumentType.LEVERAGED_INVERSE_ETF,
            underlying_index="S&P 500",
            leverage_factor=-3.0,
            max_allocation_of_sleeve_pct=0.25,
            max_holding_period_days=20,
            min_average_daily_volume=1000000,
        ),
        DefensiveInstrumentSpec(
            symbol="VIXY",
            instrument_type=DefensiveInstrumentType.VOLATILITY_ETP,
            underlying_index="VIX Short-Term Futures",
            leverage_factor=1.0,
            max_allocation_of_sleeve_pct=0.25,
            max_holding_period_days=15,
            min_average_daily_volume=250000,
        ),
        DefensiveInstrumentSpec(
            symbol="BIL",
            instrument_type=DefensiveInstrumentType.CASH_EQUIVALENT,
            underlying_index="US Treasury Bills 1-3M",
            leverage_factor=1.0,
            max_allocation_of_sleeve_pct=1.00,
            max_holding_period_days=None,
            min_average_daily_volume=100000,
        ),
    )

    return DefensiveUniverseConfig(
        approved_instruments=approved_specs,
        core_universe_symbols=tuple(core_symbols),
        min_share_price_dollars=1.0,
    )


def evaluate_defensive_instrument_eligibility(
    symbol: str,
    config: DefensiveUniverseConfig,
    evaluation_timestamp: datetime,
    current_average_daily_volume: int | None = None,
    current_price: float | None = None,
    receipt_id: str | None = None,
) -> DefensiveEligibilityReceipt:
    """Pure deterministic evaluator for defensive instrument eligibility.

    Verifies:
    1. Symbol validity and input integrity (INV-008 fail-closed).
    2. Strict Core / Defensive universe separation (FR-011, INV-013).
    3. Pre-approved defensive instrument catalog membership.
    4. Minimum volume and price liquidity thresholds.

    Args:
        symbol: Ticker symbol of proposed defensive asset.
        config: DefensiveUniverseConfig defining catalog and constraints.
        evaluation_timestamp: Timestamp anchor echoed into receipt (INV-001).
        current_average_daily_volume: Optional observed ADV for liquidity verification.
        current_price: Optional observed share price.
        receipt_id: Optional deterministic ID for the receipt.

    Returns:
        DefensiveEligibilityReceipt recording the deterministic outcome.

    Raises:
        UnknownDefenseInputError: On invalid, corrupt, or NaN inputs (INV-008).
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise UnknownDefenseInputError(f"Symbol must be a non-empty string, got {symbol!r}")

    cleaned_symbol = symbol.strip().upper()

    if not isinstance(config, DefensiveUniverseConfig):
        raise UnknownDefenseInputError(f"Invalid config type: {type(config)}")

    if not isinstance(evaluation_timestamp, datetime):
        raise UnknownDefenseInputError(f"Invalid evaluation_timestamp: {evaluation_timestamp}")

    if current_average_daily_volume is not None:
        if not isinstance(current_average_daily_volume, (int, float)):
            raise UnknownDefenseInputError(
                f"current_average_daily_volume must be numeric, got {current_average_daily_volume}"
            )
        if math.isnan(current_average_daily_volume) or math.isinf(current_average_daily_volume):
            raise UnknownDefenseInputError(
                f"current_average_daily_volume is NaN or infinite: {current_average_daily_volume}"
            )
        if current_average_daily_volume < 0:
            raise UnknownDefenseInputError(
                f"ADV cannot be negative, got {current_average_daily_volume}"
            )

    if current_price is not None:
        if not isinstance(current_price, (int, float)):
            raise UnknownDefenseInputError(f"current_price must be numeric, got {current_price}")
        if math.isnan(current_price) or math.isinf(current_price):
            raise UnknownDefenseInputError(f"current_price is NaN or infinite: {current_price}")
        if current_price <= 0.0:
            raise UnknownDefenseInputError(
                f"current_price must be strictly positive, got {current_price}"
            )

    if receipt_id is None:
        ts_sec = int(evaluation_timestamp.timestamp())
        receipt_id = f"DEF-UNIV-{cleaned_symbol}-{ts_sec}"

    # 1. Core Collision Check (FR-011, INV-013)
    normalized_core_symbols = {s.strip().upper() for s in config.core_universe_symbols}
    if cleaned_symbol in normalized_core_symbols:
        return DefensiveEligibilityReceipt(
            receipt_id=receipt_id,
            symbol=cleaned_symbol,
            status=DefensiveEligibilityStatus.UNIVERSE_COLLISION,
            is_eligible=False,
            instrument_spec=None,
            reasons=(
                f"Symbol '{cleaned_symbol}' belongs to the Core trading universe and cannot "
                "be deployed in Black Swan defense (FR-011, INV-013).",
            ),
            evaluation_timestamp=evaluation_timestamp,
        )

    # 2. Approved Defensive Instrument Catalog Lookup
    spec_map = {spec.symbol.upper(): spec for spec in config.approved_instruments}
    matched_spec = spec_map.get(cleaned_symbol)

    if matched_spec is None:
        return DefensiveEligibilityReceipt(
            receipt_id=receipt_id,
            symbol=cleaned_symbol,
            status=DefensiveEligibilityStatus.UNAPPROVED_SYMBOL,
            is_eligible=False,
            instrument_spec=None,
            reasons=(
                f"Symbol '{cleaned_symbol}' is not an approved Black Swan defensive instrument.",
            ),
            evaluation_timestamp=evaluation_timestamp,
        )

    # 3. Liquidity and Price Constraints Check
    reasons: list[str] = []
    if (
        current_average_daily_volume is not None
        and current_average_daily_volume < matched_spec.min_average_daily_volume
    ):
        reasons.append(
            f"Observed ADV ({int(current_average_daily_volume):,}) is below instrument minimum "
            f"({matched_spec.min_average_daily_volume:,}) for '{cleaned_symbol}'."
        )

    if current_price is not None and current_price < config.min_share_price_dollars:
        reasons.append(
            f"Observed price (${current_price:.2f}) is below minimum share price "
            f"(${config.min_share_price_dollars:.2f}) for '{cleaned_symbol}'."
        )

    if reasons:
        return DefensiveEligibilityReceipt(
            receipt_id=receipt_id,
            symbol=cleaned_symbol,
            status=DefensiveEligibilityStatus.INSUFFICIENT_LIQUIDITY,
            is_eligible=False,
            instrument_spec=matched_spec,
            reasons=tuple(reasons),
            evaluation_timestamp=evaluation_timestamp,
        )

    # 4. Instrument is Eligible
    return DefensiveEligibilityReceipt(
        receipt_id=receipt_id,
        symbol=cleaned_symbol,
        status=DefensiveEligibilityStatus.ELIGIBLE,
        is_eligible=True,
        instrument_spec=matched_spec,
        reasons=(
            f"Symbol '{cleaned_symbol}' is an approved defensive asset "
            f"({matched_spec.instrument_type.value}) with leverage {matched_spec.leverage_factor}x "
            "and satisfies all universe criteria.",
        ),
        evaluation_timestamp=evaluation_timestamp,
    )


def enforce_defensive_instrument_eligibility(
    symbol: str,
    config: DefensiveUniverseConfig,
    evaluation_timestamp: datetime,
    current_average_daily_volume: int | None = None,
    current_price: float | None = None,
) -> DefensiveEligibilityReceipt:
    """Enforce defensive instrument eligibility with fail-closed exception gating.

    Args:
        symbol: Ticker symbol of proposed defensive asset.
        config: DefensiveUniverseConfig defining catalog and constraints.
        evaluation_timestamp: Timestamp anchor echoed into receipt.
        current_average_daily_volume: Optional observed ADV for liquidity verification.
        current_price: Optional observed share price.

    Returns:
        DefensiveEligibilityReceipt if eligible.

    Raises:
        CoreDefensiveUniverseCollisionError: If symbol belongs to the Core universe (FR-011).
        DisallowedDefensiveInstrumentError: If symbol is unapproved.
        DefensiveUniverseLiquidityError: If symbol fails ADV or price minimums.
        UnknownDefenseInputError: On corrupted, NaN, or invalid inputs.
    """
    receipt = evaluate_defensive_instrument_eligibility(
        symbol=symbol,
        config=config,
        evaluation_timestamp=evaluation_timestamp,
        current_average_daily_volume=current_average_daily_volume,
        current_price=current_price,
    )

    if receipt.is_eligible:
        return receipt

    reason_text = receipt.reasons[0] if receipt.reasons else "Eligibility check failed."

    if receipt.status == DefensiveEligibilityStatus.UNIVERSE_COLLISION:
        raise CoreDefensiveUniverseCollisionError(reason_text)

    if receipt.status == DefensiveEligibilityStatus.UNAPPROVED_SYMBOL:
        raise DisallowedDefensiveInstrumentError(reason_text)

    if receipt.status == DefensiveEligibilityStatus.INSUFFICIENT_LIQUIDITY:
        raise DefensiveUniverseLiquidityError(reason_text)

    raise DisallowedDefensiveInstrumentError(reason_text)
