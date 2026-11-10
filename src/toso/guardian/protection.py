"""TOSO Protection Contract & Deterministic Recovery Engine.

Implements FR-014, INV-001, INV-006, INV-008:
- Mandatory verified protection for all open positions.
- Immediate block on additional exposure when protection is unverified (AC-001).
- Deterministic recovery workflow on missing, stale, or dropped stops (AC-002).
- Automatic transition to safety liquidation if recovery fails (AC-003).
- Explicit gap/slippage loss modeling confirming non-guaranteed stop price fills (AC-004).
"""

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

from toso.guardian.errors import (
    InvalidProtectionStateError,
    ProtectionRecoveryFailedError,
    UnverifiedProtectionError,
)
from toso.guardian.models import (
    ProtectionAction,
    ProtectionContract,
    ProtectionDecision,
    ProtectionStatus,
)

EPSILON: float = 1e-6


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric inputs for fail-closed handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise InvalidProtectionStateError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise InvalidProtectionStateError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise InvalidProtectionStateError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise InvalidProtectionStateError(f"Field '{name}' cannot be negative, got {val}")


def verify_position_protection(
    position_id: str,
    symbol: str,
    shares: int,
    invalidation_price: float,
    active_stop_order: dict[str, Any] | None,
    as_of_timestamp: datetime,
    existing_contract: ProtectionContract | None = None,
) -> ProtectionContract:
    """Evaluate active broker stop order state against required invalidation.

    Guarantees pure deterministic verification without wall-clock reads (INV-001, INV-006).
    """
    if not position_id or not isinstance(position_id, str) or not position_id.strip():
        raise InvalidProtectionStateError("position_id must be a non-empty string.")
    if not symbol or not isinstance(symbol, str) or not symbol.strip():
        raise InvalidProtectionStateError("symbol must be a non-empty string.")
    if not isinstance(shares, int) or shares <= 0:
        raise InvalidProtectionStateError(
            f"shares must be a strictly positive integer, got {shares}."
        )

    _validate_float(invalidation_price, "invalidation_price", must_be_positive=True)

    recovery_attempts = existing_contract.recovery_attempts if existing_contract else 0
    max_recovery_attempts = existing_contract.max_recovery_attempts if existing_contract else 2

    # If no stop order is found at the broker
    if active_stop_order is None:
        return ProtectionContract(
            position_id=position_id,
            symbol=symbol,
            shares=shares,
            status=ProtectionStatus.UNPROTECTED,
            active_stop_order_id=None,
            active_stop_price=None,
            invalidation_price=invalidation_price,
            recovery_attempts=recovery_attempts,
            max_recovery_attempts=max_recovery_attempts,
            is_verified=False,
            last_verified_at=as_of_timestamp,
        )

    # Stop order provided: validate fields
    stop_id = str(active_stop_order.get("order_id", ""))
    stop_price = active_stop_order.get("stop_price")
    stop_shares = active_stop_order.get("shares")
    order_status = active_stop_order.get("status", "ACTIVE")

    if not stop_id or stop_price is None or stop_shares is None:
        return ProtectionContract(
            position_id=position_id,
            symbol=symbol,
            shares=shares,
            status=ProtectionStatus.AMBIGUOUS,
            active_stop_order_id=stop_id or None,
            active_stop_price=float(stop_price) if isinstance(stop_price, (int, float)) else None,
            invalidation_price=invalidation_price,
            recovery_attempts=recovery_attempts,
            max_recovery_attempts=max_recovery_attempts,
            is_verified=False,
            last_verified_at=as_of_timestamp,
        )

    _validate_float(float(stop_price), "active_stop_order.stop_price", must_be_positive=True)
    stop_price_f = float(stop_price)
    stop_shares_i = int(stop_shares)

    # Verify stop price is at least as tight as invalidation (>= invalidation - EPSILON)
    # and covers full position quantity
    is_price_compliant = stop_price_f >= invalidation_price - EPSILON
    is_quantity_compliant = stop_shares_i >= shares
    is_status_active = order_status in ("ACTIVE", "OPEN", "SUBMITTED")

    if is_price_compliant and is_quantity_compliant and is_status_active:
        status = ProtectionStatus.VERIFIED
        is_verified = True
    else:
        status = ProtectionStatus.UNPROTECTED
        is_verified = False

    return ProtectionContract(
        position_id=position_id,
        symbol=symbol,
        shares=shares,
        status=status,
        active_stop_order_id=stop_id,
        active_stop_price=stop_price_f,
        invalidation_price=invalidation_price,
        recovery_attempts=recovery_attempts,
        max_recovery_attempts=max_recovery_attempts,
        is_verified=is_verified,
        last_verified_at=as_of_timestamp,
    )


def evaluate_protection_contract(
    contract: ProtectionContract,
    evaluation_timestamp: datetime,
    max_staleness_seconds: float = 60.0,
) -> ProtectionDecision:
    """Deterministically evaluate required safety action under the Protection Contract."""
    _validate_float(max_staleness_seconds, "max_staleness_seconds", must_be_positive=True)

    staleness_seconds = (evaluation_timestamp - contract.last_verified_at).total_seconds()
    is_stale = staleness_seconds > max_staleness_seconds or staleness_seconds < 0.0

    decision_id = f"PROT-{contract.position_id}-{int(evaluation_timestamp.timestamp())}"

    if contract.status == ProtectionStatus.VERIFIED:
        if is_stale:
            return ProtectionDecision(
                decision_id=decision_id,
                position_id=contract.position_id,
                symbol=contract.symbol,
                current_status=contract.status,
                action=ProtectionAction.ATTEMPT_RECOVERY,
                can_add_exposure=False,
                reason=(
                    f"Protection verification is stale ({staleness_seconds:.1f}s > "
                    f"{max_staleness_seconds:.1f}s); additional exposure blocked."
                ),
                timestamp=evaluation_timestamp,
            )
        return ProtectionDecision(
            decision_id=decision_id,
            position_id=contract.position_id,
            symbol=contract.symbol,
            current_status=contract.status,
            action=ProtectionAction.MAINTAIN_PROTECTION,
            can_add_exposure=True,
            reason="Position has verified active stop protection.",
            timestamp=evaluation_timestamp,
        )

    if contract.status in (ProtectionStatus.UNPROTECTED, ProtectionStatus.AMBIGUOUS):
        if contract.recovery_attempts < contract.max_recovery_attempts:
            return ProtectionDecision(
                decision_id=decision_id,
                position_id=contract.position_id,
                symbol=contract.symbol,
                current_status=contract.status,
                action=ProtectionAction.ATTEMPT_RECOVERY,
                can_add_exposure=False,
                reason=(
                    f"Protection is {contract.status.value}; attempting deterministic recovery "
                    f"({contract.recovery_attempts + 1}/{contract.max_recovery_attempts})."
                ),
                timestamp=evaluation_timestamp,
            )
        return ProtectionDecision(
            decision_id=decision_id,
            position_id=contract.position_id,
            symbol=contract.symbol,
            current_status=contract.status,
            action=ProtectionAction.TRIGGER_SAFETY_LIQUIDATION,
            can_add_exposure=False,
            reason=(
                f"Protection recovery attempts exhausted ({contract.recovery_attempts}/"
                f"{contract.max_recovery_attempts}); initiating safety liquidation."
            ),
            timestamp=evaluation_timestamp,
        )

    # RECOVERY_FAILED or LIQUIDATING
    return ProtectionDecision(
        decision_id=decision_id,
        position_id=contract.position_id,
        symbol=contract.symbol,
        current_status=contract.status,
        action=ProtectionAction.TRIGGER_SAFETY_LIQUIDATION,
        can_add_exposure=False,
        reason=f"Position in {contract.status.value} state; safety liquidation mandatory.",
        timestamp=evaluation_timestamp,
    )


def can_receive_additional_exposure(contract: ProtectionContract) -> bool:
    """Return True if and only if position has verified protection (AC-001)."""
    return contract.status == ProtectionStatus.VERIFIED and contract.is_verified


def enforce_can_receive_additional_exposure(contract: ProtectionContract) -> None:
    """Raise UnverifiedProtectionError if position does not have verified protection (AC-001)."""
    if not can_receive_additional_exposure(contract):
        raise UnverifiedProtectionError(
            f"Position '{contract.position_id}' ({contract.symbol}) has unverified protection "
            f"({contract.status.value}). Additional exposure strictly blocked."
        )


def execute_protection_recovery(
    contract: ProtectionContract,
    submit_stop_fn: Callable[[str, str, int, float], str],
    emergency_exit_fn: Callable[[str, str, int], Any],
    evaluation_timestamp: datetime,
) -> tuple[ProtectionContract, Any]:
    """Execute deterministic protection recovery workflow (AC-002, AC-003).

    Returns:
        tuple of (updated_contract, action_result)
    """
    decision = evaluate_protection_contract(contract, evaluation_timestamp)

    if decision.action == ProtectionAction.TRIGGER_SAFETY_LIQUIDATION:
        exit_res = emergency_exit_fn(contract.position_id, contract.symbol, contract.shares)
        updated_contract = ProtectionContract(
            position_id=contract.position_id,
            symbol=contract.symbol,
            shares=contract.shares,
            status=ProtectionStatus.LIQUIDATING,
            active_stop_order_id=None,
            active_stop_price=None,
            invalidation_price=contract.invalidation_price,
            recovery_attempts=contract.recovery_attempts,
            max_recovery_attempts=contract.max_recovery_attempts,
            is_verified=False,
            last_verified_at=evaluation_timestamp,
        )
        return updated_contract, exit_res

    # Action is ATTEMPT_RECOVERY
    try:
        new_stop_id = submit_stop_fn(
            contract.position_id,
            contract.symbol,
            contract.shares,
            contract.invalidation_price,
        )
        updated_contract = ProtectionContract(
            position_id=contract.position_id,
            symbol=contract.symbol,
            shares=contract.shares,
            status=ProtectionStatus.VERIFIED,
            active_stop_order_id=new_stop_id,
            active_stop_price=contract.invalidation_price,
            invalidation_price=contract.invalidation_price,
            recovery_attempts=contract.recovery_attempts + 1,
            max_recovery_attempts=contract.max_recovery_attempts,
            is_verified=True,
            last_verified_at=evaluation_timestamp,
        )
        return updated_contract, {"status": "STOP_RECOVERED", "order_id": new_stop_id}
    except Exception as exc:
        new_attempts = contract.recovery_attempts + 1
        if new_attempts >= contract.max_recovery_attempts:
            exit_res = emergency_exit_fn(contract.position_id, contract.symbol, contract.shares)
            updated_contract = ProtectionContract(
                position_id=contract.position_id,
                symbol=contract.symbol,
                shares=contract.shares,
                status=ProtectionStatus.LIQUIDATING,
                active_stop_order_id=None,
                active_stop_price=None,
                invalidation_price=contract.invalidation_price,
                recovery_attempts=new_attempts,
                max_recovery_attempts=contract.max_recovery_attempts,
                is_verified=False,
                last_verified_at=evaluation_timestamp,
            )
            return updated_contract, exit_res

        updated_contract = ProtectionContract(
            position_id=contract.position_id,
            symbol=contract.symbol,
            shares=contract.shares,
            status=ProtectionStatus.RECOVERY_FAILED,
            active_stop_order_id=None,
            active_stop_price=None,
            invalidation_price=contract.invalidation_price,
            recovery_attempts=new_attempts,
            max_recovery_attempts=contract.max_recovery_attempts,
            is_verified=False,
            last_verified_at=evaluation_timestamp,
        )
        raise ProtectionRecoveryFailedError(
            f"Recovery failed on attempt {new_attempts}/{contract.max_recovery_attempts}: {exc}"
        ) from exc


def calculate_gap_scenario_exposure(
    contract: ProtectionContract,
    entry_price: float,
    gap_down_pct: float,
) -> float:
    """Model adverse gap scenario loss demonstrating non-guaranteed fill condition (AC-004).

    Protection does not guarantee a fill at the stop price during adverse gap conditions (FR-014).
    """
    _validate_float(entry_price, "entry_price", must_be_positive=True)
    _validate_float(gap_down_pct, "gap_down_pct", must_be_positive=True)

    nominal_loss_per_share = max(0.0, entry_price - contract.invalidation_price)
    gap_loss_per_share = round(entry_price * gap_down_pct, 4)
    effective_loss_per_share = max(gap_loss_per_share, nominal_loss_per_share)

    return round(effective_loss_per_share * contract.shares, 4)
