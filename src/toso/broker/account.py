"""TOSO Broker Account Reconciliation Engine.

Implements FR-013, INV-009, INV-010, INV-008, INV-001, INV-006:
- Broker state is authoritative for account balances, equity, and cash (INV-009).
- Ambiguous or conflicting outcomes are detected and gated before action (INV-010).
- Corrupted, missing, negative, NaN, or stale data fails closed (INV-008).
- Pure determinism and audit receipts without wall-clock reads (INV-001, INV-006).
- Synchronization helper applying reconciled broker truth to PortfolioStateSnapshot.
"""

import math
from datetime import datetime

from toso.broker.errors import (
    AccountReconciliationError,
    BrokerDisconnectedError,
    MaterialAccountDiscrepancyError,
    StaleBrokerAccountDataError,
    UnknownBrokerAccountInputError,
)
from toso.broker.models import (
    AccountDiscrepancy,
    AccountDiscrepancyType,
    AccountReconciliationReceipt,
    AccountReconciliationStatus,
    BrokerAccountSnapshot,
    LocalAccountSnapshot,
)
from toso.guardian.models import PortfolioStateSnapshot


def _validate_float(val: float, name: str, must_be_positive: bool = True) -> None:
    """Validate numeric values for fail-closed UNKNOWN handling (INV-008)."""
    if val is None or not isinstance(val, (int, float)):
        raise UnknownBrokerAccountInputError(f"Field '{name}' is missing or non-numeric: {val}")
    if math.isnan(val) or math.isinf(val):
        raise UnknownBrokerAccountInputError(f"Field '{name}' is NaN or infinite: {val}")
    if must_be_positive and val <= 0.0:
        raise UnknownBrokerAccountInputError(f"Field '{name}' must be strictly positive, got {val}")
    if not must_be_positive and val < 0.0:
        raise UnknownBrokerAccountInputError(f"Field '{name}' cannot be negative, got {val}")


def reconcile_account_state(
    broker_snapshot: BrokerAccountSnapshot,
    local_snapshot: LocalAccountSnapshot,
    reconciliation_timestamp: datetime,
    max_staleness_seconds: float = 60.0,
    tolerance_dollars: float = 0.01,
    receipt_id: str | None = None,
) -> AccountReconciliationReceipt:
    """Deterministically reconcile local account tracking with authoritative broker data.

    Enforces INV-009: When local state conflicts with confirmed broker state, broker
    state governs actual account truth.

    Args:
        broker_snapshot: Authoritative balance snapshot from the broker.
        local_snapshot: Locally recorded balance snapshot.
        reconciliation_timestamp: Pure clock anchor for this evaluation.
        max_staleness_seconds: Maximum allowable age of broker snapshot.
        tolerance_dollars: Threshold for immaterial differences (e.g. micro-interest).
        receipt_id: Optional explicit receipt identifier.

    Returns:
        Immutable AccountReconciliationReceipt detailing discrepancies and authoritative numbers.

    Raises:
        UnknownBrokerAccountInputError: If any numeric or date inputs are invalid or NaN.
    """
    if not isinstance(reconciliation_timestamp, datetime):
        raise UnknownBrokerAccountInputError(
            f"reconciliation_timestamp must be a datetime, got {reconciliation_timestamp}"
        )

    # 1. Fail-closed input validations (INV-008)
    _validate_float(broker_snapshot.total_equity, "broker.total_equity", must_be_positive=True)
    _validate_float(broker_snapshot.cash_balance, "broker.cash_balance", must_be_positive=False)
    _validate_float(broker_snapshot.buying_power, "broker.buying_power", must_be_positive=False)
    _validate_float(local_snapshot.total_equity, "local.total_equity", must_be_positive=True)
    _validate_float(local_snapshot.cash_balance, "local.cash_balance", must_be_positive=False)
    _validate_float(max_staleness_seconds, "max_staleness_seconds", must_be_positive=True)
    _validate_float(tolerance_dollars, "tolerance_dollars", must_be_positive=False)

    discrepancies: list[AccountDiscrepancy] = []
    diagnostic_reasons: list[str] = []

    # 2. Check Broker Connectivity
    if not broker_snapshot.is_connected:
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.STALE_DATA,
                local_value="CONNECTED",
                broker_value="DISCONNECTED",
                delta=None,
                is_material=True,
                detail="Broker connection is disconnected or offline.",
            )
        )
        diagnostic_reasons.append("Broker connection is offline or unavailable.")

    # 3. Check Data Staleness
    staleness_seconds = (reconciliation_timestamp - broker_snapshot.timestamp).total_seconds()
    if staleness_seconds < 0.0:
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.STALE_DATA,
                local_value=str(reconciliation_timestamp),
                broker_value=str(broker_snapshot.timestamp),
                delta=None,
                is_material=True,
                detail="Broker snapshot timestamp is in the future relative to evaluation.",
            )
        )
        diagnostic_reasons.append(
            f"Broker timestamp {broker_snapshot.timestamp} is in future "
            f"relative to {reconciliation_timestamp}."
        )
    elif staleness_seconds > max_staleness_seconds:
        detail_str = (
            f"Broker data age {staleness_seconds:.1f}s exceeds max {max_staleness_seconds:.1f}s."
        )
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.STALE_DATA,
                local_value=f"{staleness_seconds:.1f}s",
                broker_value=f"{max_staleness_seconds:.1f}s",
                delta=staleness_seconds - max_staleness_seconds,
                is_material=True,
                detail=detail_str,
            )
        )
        diagnostic_reasons.append(
            f"Broker data is stale ({staleness_seconds:.1f}s > {max_staleness_seconds:.1f}s)."
        )

    # 4. Check Currency Alignment
    if broker_snapshot.currency.upper() != local_snapshot.currency.upper():
        curr_detail = (
            f"Currency mismatch: local {local_snapshot.currency} vs "
            f"broker {broker_snapshot.currency}."
        )
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.CURRENCY_MISMATCH,
                local_value=local_snapshot.currency,
                broker_value=broker_snapshot.currency,
                delta=None,
                is_material=True,
                detail=curr_detail,
            )
        )
        diagnostic_reasons.append(
            f"Currency mismatch ({local_snapshot.currency} != {broker_snapshot.currency})."
        )

    # 5. Check Equity Discrepancy
    equity_delta = round(broker_snapshot.total_equity - local_snapshot.total_equity, 4)
    if abs(equity_delta) > tolerance_dollars:
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.EQUITY_MISMATCH,
                local_value=local_snapshot.total_equity,
                broker_value=broker_snapshot.total_equity,
                delta=equity_delta,
                is_material=True,
                detail=(
                    f"Equity mismatch: local ${local_snapshot.total_equity:.2f} vs "
                    f"broker ${broker_snapshot.total_equity:.2f} (delta ${equity_delta:+.2f})."
                ),
            )
        )
        diagnostic_reasons.append(
            f"Total equity differs by ${equity_delta:+.2f} (tolerance: ${tolerance_dollars:.2f})."
        )

    # 6. Check Cash Balance Discrepancy
    cash_delta = round(broker_snapshot.cash_balance - local_snapshot.cash_balance, 4)
    if abs(cash_delta) > tolerance_dollars:
        discrepancies.append(
            AccountDiscrepancy(
                discrepancy_type=AccountDiscrepancyType.CASH_MISMATCH,
                local_value=local_snapshot.cash_balance,
                broker_value=broker_snapshot.cash_balance,
                delta=cash_delta,
                is_material=True,
                detail=(
                    f"Cash mismatch: local ${local_snapshot.cash_balance:.2f} vs "
                    f"broker ${broker_snapshot.cash_balance:.2f} (delta ${cash_delta:+.2f})."
                ),
            )
        )
        diagnostic_reasons.append(
            f"Cash balance differs by ${cash_delta:+.2f} (tolerance: ${tolerance_dollars:.2f})."
        )

    # 7. Determine Overall Reconciliation Status
    if not broker_snapshot.is_connected:
        status = AccountReconciliationStatus.BROKER_UNAVAILABLE
    elif staleness_seconds < 0.0 or staleness_seconds > max_staleness_seconds:
        status = AccountReconciliationStatus.STALE_SNAPSHOT
    elif any(d.is_material for d in discrepancies):
        status = AccountReconciliationStatus.DISCREPANCY_DETECTED
    else:
        status = AccountReconciliationStatus.RECONCILED

    # 8. Deterministic Receipt ID & Output
    if receipt_id is None:
        receipt_id = (
            f"RECON-ACCT-{broker_snapshot.account_id}-{int(reconciliation_timestamp.timestamp())}"
        )

    # INV-009: Authoritative values are always governed by the broker
    authoritative_equity = broker_snapshot.total_equity
    authoritative_cash = broker_snapshot.cash_balance

    return AccountReconciliationReceipt(
        receipt_id=receipt_id,
        timestamp=reconciliation_timestamp,
        status=status,
        broker_account_id=broker_snapshot.account_id,
        authoritative_equity=authoritative_equity,
        authoritative_cash=authoritative_cash,
        discrepancies=tuple(discrepancies),
        is_synchronized=status == AccountReconciliationStatus.RECONCILED,
        diagnostic_reasons=tuple(diagnostic_reasons),
    )


def enforce_account_reconciliation(receipt: AccountReconciliationReceipt) -> None:
    """Enforce that account state is cleanly reconciled before allowing execution (INV-010).

    Raises:
        BrokerDisconnectedError: If broker is offline.
        StaleBrokerAccountDataError: If broker snapshot is stale.
        MaterialAccountDiscrepancyError: If account state has material discrepancies.
        AccountReconciliationError: If reconciliation failed for any other reason.
    """
    if receipt.status == AccountReconciliationStatus.RECONCILED:
        return

    detail_msg = "; ".join(receipt.diagnostic_reasons) or receipt.status.value

    if receipt.status == AccountReconciliationStatus.BROKER_UNAVAILABLE:
        raise BrokerDisconnectedError(
            f"Account reconciliation failed: Broker connection unavailable. {detail_msg}"
        )
    if receipt.status == AccountReconciliationStatus.STALE_SNAPSHOT:
        raise StaleBrokerAccountDataError(
            f"Account reconciliation failed: Broker data is stale. {detail_msg}"
        )
    if receipt.status == AccountReconciliationStatus.DISCREPANCY_DETECTED:
        raise MaterialAccountDiscrepancyError(
            f"Account reconciliation failed: Discrepancy detected. {detail_msg}"
        )
    raise AccountReconciliationError(
        f"Account reconciliation failed with status '{receipt.status.value}'. {detail_msg}"
    )


def apply_reconciled_account_to_portfolio_state(
    portfolio_state: PortfolioStateSnapshot,
    reconciliation_receipt: AccountReconciliationReceipt,
) -> PortfolioStateSnapshot:
    """Synchronize reconciled authoritative broker account state into PortfolioStateSnapshot.

    Enforces INV-009: Local portfolio equity and cash balances are updated to match
    authoritative broker figures, ensuring downstream Guardian and Trader engines
    operate on authoritative reconciled truth.
    """
    new_peak = max(
        portfolio_state.peak_portfolio_equity,
        reconciliation_receipt.authoritative_equity,
    )
    return PortfolioStateSnapshot(
        portfolio_equity=reconciliation_receipt.authoritative_equity,
        peak_portfolio_equity=new_peak,
        cash_balance=reconciliation_receipt.authoritative_cash,
        positions=portfolio_state.positions,
        timestamp=reconciliation_receipt.timestamp,
    )
