"""Independent Checker Evidence Generator for Story PAPER-004."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.errors import (
    InvalidApprovalTokenError,
    JournalTamperError,
    ScientistExecutionBreachError,
    UnauthorizedProductionChangeError,
)
from toso.scientist.journal import TradeJournal
from toso.scientist.models import (
    HumanApprovalToken,
    JournalEventType,
    ScientistRole,
)
from toso.signals.models import Bar

EVIDENCE_DIR = Path("evidence")
EVIDENCE_DIR.mkdir(exist_ok=True)


def generate_bars(prices: list[float], start_ts: datetime) -> list[Bar]:
    return [
        Bar(
            timestamp=start_ts + timedelta(days=i),
            open=p,
            high=p + 1.0,
            low=p - 1.0,
            close=p,
            volume=1_000_000.0,
        )
        for i, p in enumerate(prices)
    ]


cfg = CoreConfig(
    metadata=VersionMetadata(
        version="1.0.0",
        status=ApprovalStatus.APPROVED,
        approved_by="LeadRiskOfficer",
        approved_at="2026-06-01T00:00:00Z",
        checksum="a" * 64,
        description="test",
    ),
    strategy=StrategyConfig(
        strategy_id="S1",
        timeframe="1d",
        fast_ma_period=2,
        slow_ma_period=4,
        atr_period=3,
        breakout_lookback_bars=2,
        invalidation_atr_multiple=2.0,
        pyramid_min_profit_r=1.0,
        max_pyramid_adds=2,
    ),
    risk=RiskConfig(
        max_risk_per_trade_pct=0.01,
        max_aggregate_position_risk_pct=0.02,
        max_portfolio_risk_pct=0.06,
        max_position_concentration_pct=0.20,
        slippage_buffer_pct=0.001,
        emergency_drawdown_stop_pct=0.10,
    ),
    capital=CapitalAllocationConfig(
        core_allocation_pct=0.80,
        reserve_allocation_pct=0.10,
        black_swan_allocation_pct=0.10,
    ),
    universe=UniverseConfig(
        eligible_symbols=("AAPL",),
        min_average_daily_volume_shares=1000,
        min_share_price_dollars=5.0,
    ),
)
t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
runner = PaperTradingRunner(config=cfg, initial_cash=100_000.0, anchor_timestamp=t0)

# Check 1: Scientist interception
try:
    runner.submit_as_scientist("submit_order", t0, ScientistRole.RESEARCHER)
    check1 = "FAILED"
except ScientistExecutionBreachError:
    check1 = "VERIFIED"

# Check 2: Unapproved proposal rejection
prop = create_production_proposal("P1", "H1", {"fast_ma_period": 3}, "test", t0)
try:
    runner.apply_proposal(prop, t0)
    check2 = "FAILED"
except UnauthorizedProductionChangeError:
    check2 = "VERIFIED"

# Check 3: Proposal parameter tampering after approval
sig = generate_human_approval_signature("T1", "CRO", prop.proposal_hash, t0)
tok = HumanApprovalToken(
    token_id="T1",
    approver_id="CRO",
    target_proposal_hash=prop.proposal_hash,
    approved_at=t0,
    expires_at=t0 + timedelta(days=1),
    signature=sig,
)
approved_prop = attach_human_approval(prop, tok, t0 + timedelta(minutes=1))
tampered_approved = approved_prop.model_copy(
    update={"proposed_parameters": {"fast_ma_period": 999}}
)
try:
    runner.apply_proposal(tampered_approved, t0 + timedelta(minutes=2))
    check3 = "FAILED"
except InvalidApprovalTokenError:
    check3 = "VERIFIED"

# Check 4: Journal hash chain verification
receipt = runner.journal.verify_integrity(t0)
check4 = "VERIFIED" if receipt.is_valid else "FAILED"

# Check 5: Tamper detection on modified payload
j2 = TradeJournal()
j2.record_event(JournalEventType.CONFIGURATION_LOADED, {"cash": 1000.0}, t0)
j2._entries[0] = j2._entries[0].model_copy(update={"payload": {"cash": 9999.0}})
try:
    j2.verify_integrity(t0)
    check5 = "FAILED"
except JournalTamperError:
    check5 = "VERIFIED"

# Check 6: State reconstruction after step
prices = [100.0, 101.0, 103.0, 105.0, 108.0]
bars = generate_bars(prices, t0)
for b in bars:
    runner.step({"AAPL": b})
recon = PaperTradingRunner.reconstruct_from_journal(runner.journal)
check6 = (
    "VERIFIED"
    if (
        recon["total_entries"] == runner.journal.total_entries
        and recon["cash_balance"] == round(runner.broker.cash_balance, 4)
    )
    else "FAILED"
)

evidence = {
    "CHECK1_SCIENTIST_INTERCEPTION": check1,
    "CHECK2_UNAPPROVED_PROPOSAL_REJECTION": check2,
    "CHECK3_PROPOSAL_TAMPERING_REJECTION": check3,
    "CHECK4_JOURNAL_HASH_CHAIN": check4,
    "CHECK5_TAMPER_DETECTION": check5,
    "CHECK6_STATE_RECONSTRUCTION": check6,
}
print(json.dumps(evidence, indent=2))
(EVIDENCE_DIR / "checker_paper_004_receipts.json").write_text(
    json.dumps(evidence, indent=2), encoding="utf-8"
)
