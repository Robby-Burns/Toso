"""TOSO Broker Position Reconciliation Engine.

Implements FR-013, INV-009, INV-010, INV-008, INV-001, INV-006:
- Broker state is authoritative for open positions, share quantities, and valuations (INV-009).
- Phantom positions (on broker, missing locally) are imported and protected (INV-009).
- Ghost positions (in local state, missing on broker) are dropped/closed (INV-009).
- Discrepancies are detected and gated before downstream execution (INV-010).
- Corrupted, missing, negative, NaN, or non-finite inputs fail closed (INV-008).
- Pure determinism and audit receipts without wall-clock reads (INV-001, INV-006).
- Synchronization helper applying reconciled positions to PortfolioStateSnapshot.
"""

import math
from collections.abc import Sequence
from datetime import datetime

from toso.broker.errors import (
    GhostPositionDetectedError,
    MaterialPositionDiscrepancyError,
    PhantomPositionDetectedError,
    UnknownBrokerPositionInputError,
)
from toso.broker.models import (
    BrokerPositionSnapshot,
    PositionDiscrepancy,
    PositionDiscrepancyType,
    PositionReconciliationReceipt,
    PositionReconciliationStatus,
)
from toso.guardian.models import CapitalSleeve, PortfolioStateSnapshot, PositionRiskSnapshot


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownBrokerPositionInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownBrokerPositionInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownBrokerPositionInputError(
            f"Field '{name}' must be strictly positive, got {val}"
        )
    if not must_be_positive and val < 0.0:
        raise UnknownBrokerPositionInputError(f"Field '{name}' cannot be negative, got {val}")


def _validate_broker_position(pos: BrokerPositionSnapshot) -> None:
    """Validate a broker position snapshot strictly (INV-008)."""
    if not isinstance(pos, BrokerPositionSnapshot):
        raise UnknownBrokerPositionInputError(f"Expected BrokerPositionSnapshot, got {type(pos)}")
    if not pos.symbol or not isinstance(pos.symbol, str):
        raise UnknownBrokerPositionInputError(f"Position symbol is missing or empty: {pos.symbol}")
    if not isinstance(pos.shares, int) or pos.shares <= 0:
        raise UnknownBrokerPositionInputError(
            f"Position shares for {pos.symbol} must be a positive integer, got {pos.shares}"
        )
    _validate_float(pos.current_price, f"{pos.symbol}.current_price", must_be_positive=True)
    _validate_float(pos.market_value, f"{pos.symbol}.market_value", must_be_positive=True)
    if pos.cost_basis is not None:
        _validate_float(pos.cost_basis, f"{pos.symbol}.cost_basis", must_be_positive=False)


def reconcile_positions(
    local_positions: Sequence[PositionRiskSnapshot],
    broker_positions: Sequence[BrokerPositionSnapshot],
    reconciliation_timestamp: datetime,
    default_sleeve_for_phantom: CapitalSleeve = CapitalSleeve.CORE,
    default_stop_pct: float = 0.05,
    tolerance_market_value: float = 0.01,
    auto_govern: bool = True,
    receipt_id: str | None = None,
) -> PositionReconciliationReceipt:
    """Reconcile local open positions against authoritative broker positions.

    Enforces INV-009 (Broker Authority):
    - Confirmed broker holdings strictly govern actual positions.
    - Phantom positions on broker are imported into authoritative positions.
    - Ghost positions locally missing on broker are closed/dropped.
    - Share count drift adopts broker's authoritative quantity.

    Args:
        local_positions: Locally tracked open position risk snapshots.
        broker_positions: Authoritative broker position snapshots.
        reconciliation_timestamp: Clock anchor echoed into the audit receipt (INV-006).
        default_sleeve_for_phantom: Sleeve assigned to imported phantom positions.
        default_stop_pct: Default stop percentage applied to imported phantom positions.
        tolerance_market_value: Valuation difference threshold for reporting value drift.
        auto_govern: If True, status is RECONCILED_BROKER_GOVERNED when discrepancies are resolved.
        receipt_id: Optional explicit receipt ID. Generated deterministically if None.

    Returns:
        Immutable PositionReconciliationReceipt detailing discrepancies and authoritative state.

    Raises:
        UnknownBrokerPositionInputError: If input data is corrupt, NaN, infinite (INV-008).
    """
    if reconciliation_timestamp is None or not isinstance(reconciliation_timestamp, datetime):
        raise UnknownBrokerPositionInputError(
            f"Invalid reconciliation_timestamp: {reconciliation_timestamp}"
        )
    if default_stop_pct <= 0.0 or default_stop_pct >= 1.0:
        raise UnknownBrokerPositionInputError(
            f"default_stop_pct must be between 0.0 and 1.0, got {default_stop_pct}"
        )

    # Validate all broker positions
    for bp in broker_positions:
        _validate_broker_position(bp)

    # Check for duplicate symbols
    local_symbols = [lp.symbol for lp in local_positions]
    if len(local_symbols) != len(set(local_symbols)):
        raise UnknownBrokerPositionInputError("Duplicate symbols found in local_positions")

    broker_symbols = [bp.symbol for bp in broker_positions]
    if len(broker_symbols) != len(set(broker_symbols)):
        raise UnknownBrokerPositionInputError("Duplicate symbols found in broker_positions")

    local_map = {lp.symbol: lp for lp in local_positions}
    broker_map = {bp.symbol: bp for bp in broker_positions}

    all_symbols = sorted(set(local_map.keys()) | set(broker_map.keys()))

    discrepancies: list[PositionDiscrepancy] = []
    diagnostic_reasons: list[str] = []
    authoritative_positions: list[PositionRiskSnapshot] = []

    for sym in all_symbols:
        local_p = local_map.get(sym)
        broker_p = broker_map.get(sym)

        # Case 1: Present in both local and broker
        if local_p is not None and broker_p is not None:
            share_delta = broker_p.shares - local_p.shares
            value_delta = round(broker_p.market_value - local_p.market_value, 4)

            if share_delta != 0:
                # Quantity mismatch: Broker strictly governs (INV-009)
                detail = (
                    f"Share mismatch for {sym}: local has {local_p.shares}, "
                    f"broker has {broker_p.shares} (delta: {share_delta:+d})."
                )
                discrepancies.append(
                    PositionDiscrepancy(
                        symbol=sym,
                        discrepancy_type=PositionDiscrepancyType.QUANTITY_MISMATCH,
                        local_shares=local_p.shares,
                        broker_shares=broker_p.shares,
                        local_market_value=local_p.market_value,
                        broker_market_value=broker_p.market_value,
                        share_delta=share_delta,
                        value_delta=value_delta,
                        is_material=True,
                        detail=detail,
                    )
                )
                diagnostic_reasons.append(detail)

                # Authoritative position adopts broker shares and valuation (INV-009)
                new_open_risk = (
                    round(broker_p.shares * (broker_p.current_price - local_p.stop_loss_price), 4)
                    if broker_p.current_price > local_p.stop_loss_price
                    else 0.0
                )
                authoritative_positions.append(
                    PositionRiskSnapshot(
                        symbol=sym,
                        shares=broker_p.shares,
                        current_price=broker_p.current_price,
                        stop_loss_price=local_p.stop_loss_price,
                        open_risk_dollars=new_open_risk,
                        market_value=broker_p.market_value,
                        sleeve=local_p.sleeve,
                    )
                )

            elif abs(value_delta) > tolerance_market_value:
                # Market value mismatch without share drift (non-material)
                detail = (
                    f"Market value mismatch for {sym}: local has ${local_p.market_value:.2f}, "
                    f"broker has ${broker_p.market_value:.2f} (delta: ${value_delta:+.2f})."
                )
                discrepancies.append(
                    PositionDiscrepancy(
                        symbol=sym,
                        discrepancy_type=PositionDiscrepancyType.MARKET_VALUE_MISMATCH,
                        local_shares=local_p.shares,
                        broker_shares=broker_p.shares,
                        local_market_value=local_p.market_value,
                        broker_market_value=broker_p.market_value,
                        share_delta=0,
                        value_delta=value_delta,
                        is_material=False,
                        detail=detail,
                    )
                )
                diagnostic_reasons.append(detail)

                new_open_risk = (
                    round(broker_p.shares * (broker_p.current_price - local_p.stop_loss_price), 4)
                    if broker_p.current_price > local_p.stop_loss_price
                    else 0.0
                )
                authoritative_positions.append(
                    PositionRiskSnapshot(
                        symbol=sym,
                        shares=broker_p.shares,
                        current_price=broker_p.current_price,
                        stop_loss_price=local_p.stop_loss_price,
                        open_risk_dollars=new_open_risk,
                        market_value=broker_p.market_value,
                        sleeve=local_p.sleeve,
                    )
                )

            else:
                # Matched perfectly
                authoritative_positions.append(local_p)

        # Case 2: Present on broker, missing locally (Phantom Position)
        elif broker_p is not None and local_p is None:
            detail = (
                f"Phantom broker position detected for {sym}: {broker_p.shares} shares "
                f"at ${broker_p.current_price:.2f} (total: ${broker_p.market_value:.2f}) "
                "present on broker but absent from local tracking."
            )
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=sym,
                    discrepancy_type=PositionDiscrepancyType.PHANTOM_BROKER_POSITION,
                    local_shares=0,
                    broker_shares=broker_p.shares,
                    local_market_value=0.0,
                    broker_market_value=broker_p.market_value,
                    share_delta=broker_p.shares,
                    value_delta=broker_p.market_value,
                    is_material=True,
                    detail=detail,
                )
            )
            diagnostic_reasons.append(detail)

            # Broker Authority (INV-009): Must track and protect this actual holding
            stop_loss = round(broker_p.current_price * (1.0 - default_stop_pct), 4)
            open_risk = round(broker_p.shares * (broker_p.current_price - stop_loss), 4)
            authoritative_positions.append(
                PositionRiskSnapshot(
                    symbol=sym,
                    shares=broker_p.shares,
                    current_price=broker_p.current_price,
                    stop_loss_price=stop_loss,
                    open_risk_dollars=open_risk,
                    market_value=broker_p.market_value,
                    sleeve=default_sleeve_for_phantom,
                )
            )

        # Case 3: Present locally, missing on broker (Ghost Position)
        elif local_p is not None and broker_p is None:
            detail = (
                f"Ghost local position detected for {sym}: {local_p.shares} shares "
                f"recorded locally but position does not exist on broker."
            )
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=sym,
                    discrepancy_type=PositionDiscrepancyType.GHOST_LOCAL_POSITION,
                    local_shares=local_p.shares,
                    broker_shares=0,
                    local_market_value=local_p.market_value,
                    broker_market_value=0.0,
                    share_delta=-local_p.shares,
                    value_delta=-local_p.market_value,
                    is_material=True,
                    detail=detail,
                )
            )
            diagnostic_reasons.append(detail)

            # Broker Authority (INV-009): Position closed on broker, dropped from authoritative

    has_discrepancy = len(discrepancies) > 0
    has_material = any(d.is_material for d in discrepancies)

    if not has_discrepancy:
        status = PositionReconciliationStatus.RECONCILED_CLEAN
        diagnostic_reasons.append("All positions matched cleanly between local and broker feeds.")
    elif auto_govern:
        status = PositionReconciliationStatus.RECONCILED_BROKER_GOVERNED
        msg = (
            f"Detected {len(discrepancies)} discrepancies; "
            "resolved under broker authority (INV-009)."
        )
        diagnostic_reasons.append(msg)
    else:
        status = PositionReconciliationStatus.DISCREPANCY_DETECTED

    if receipt_id is None:
        ts_sec = int(reconciliation_timestamp.timestamp())
        receipt_id = f"RECON-POS-{ts_sec}-{len(discrepancies)}"

    return PositionReconciliationReceipt(
        receipt_id=receipt_id,
        timestamp=reconciliation_timestamp,
        status=status,
        authoritative_positions=tuple(authoritative_positions),
        discrepancies=tuple(discrepancies),
        has_discrepancy=has_discrepancy,
        has_material_discrepancy=has_material,
        diagnostic_reasons=tuple(diagnostic_reasons),
    )


def enforce_position_reconciliation(
    receipt: PositionReconciliationReceipt,
    allow_broker_governed: bool = False,
) -> bool:
    """Enforce position reconciliation safety gating (INV-010, INV-008).

    Validates that positions are cleanly reconciled or explicitly acknowledged before
    allowing downstream trading actions.

    Args:
        receipt: The PositionReconciliationReceipt from reconcile_positions.
        allow_broker_governed: If True, permits execution when discrepancies were reconciled
            under authoritative broker governance. If False, any material discrepancy blocks action.

    Returns:
        True if positions are safe for downstream execution.

    Raises:
        PhantomPositionDetectedError: If an unexpected phantom broker position exists.
        GhostPositionDetectedError: If an unexpected ghost local position exists.
        MaterialPositionDiscrepancyError: If a quantity or material discrepancy exists.
        UnknownBrokerPositionInputError: If status is FAILED_CLOSED.
    """
    if receipt.status == PositionReconciliationStatus.FAILED_CLOSED:
        raise UnknownBrokerPositionInputError(
            f"Position reconciliation failed closed: {receipt.diagnostic_reasons}"
        )

    if receipt.status == PositionReconciliationStatus.RECONCILED_CLEAN:
        return True

    if receipt.status == PositionReconciliationStatus.RECONCILED_BROKER_GOVERNED:
        if allow_broker_governed:
            return True

    # If we reach here, we must fail closed and identify the most specific violation
    for d in receipt.discrepancies:
        if d.discrepancy_type == PositionDiscrepancyType.PHANTOM_BROKER_POSITION:
            raise PhantomPositionDetectedError(d.detail)
        if d.discrepancy_type == PositionDiscrepancyType.GHOST_LOCAL_POSITION:
            raise GhostPositionDetectedError(d.detail)
        if d.discrepancy_type == PositionDiscrepancyType.QUANTITY_MISMATCH:
            raise MaterialPositionDiscrepancyError(d.detail)

    if receipt.has_material_discrepancy:
        raise MaterialPositionDiscrepancyError(
            f"Unresolved material position discrepancy detected: {receipt.diagnostic_reasons}"
        )

    return True


def apply_reconciled_positions_to_portfolio_state(
    portfolio_state: PortfolioStateSnapshot,
    reconciliation_receipt: PositionReconciliationReceipt,
    update_equity_from_positions: bool = False,
) -> PortfolioStateSnapshot:
    """Synchronize PortfolioStateSnapshot with reconciled authoritative positions (INV-009).

    Produces a new immutable PortfolioStateSnapshot with updated positions and timestamp.
    Optionally updates portfolio_equity and peak_portfolio_equity to reflect new market values.

    Args:
        portfolio_state: Existing PortfolioStateSnapshot.
        reconciliation_receipt: Valid PositionReconciliationReceipt.
        update_equity_from_positions: If True, recomputes total portfolio equity as
            cash_balance + sum(market_value of authoritative positions).

    Returns:
        Updated immutable PortfolioStateSnapshot.
    """
    if update_equity_from_positions:
        pos_mv = sum(p.market_value for p in reconciliation_receipt.authoritative_positions)
        new_equity = round(portfolio_state.cash_balance + pos_mv, 4)
        new_peak = max(portfolio_state.peak_portfolio_equity, new_equity)
    else:
        new_equity = portfolio_state.portfolio_equity
        new_peak = portfolio_state.peak_portfolio_equity

    return PortfolioStateSnapshot(
        portfolio_equity=new_equity,
        peak_portfolio_equity=new_peak,
        cash_balance=portfolio_state.cash_balance,
        positions=reconciliation_receipt.authoritative_positions,
        timestamp=reconciliation_receipt.timestamp,
    )
