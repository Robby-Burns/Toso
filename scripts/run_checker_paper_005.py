"""Independent Checker Evidence Generator for Story PAPER-005."""

import json
import math
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
from toso.guardian.models import SystemFeedContext
from toso.paper.errors import (
    EmergencyKillSwitchActiveError,
    UnknownPaperInputError,
)
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    attach_human_approval,
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.models import HumanApprovalToken, JournalEventType
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


def make_config(drawdown_stop: float = 0.10) -> CoreConfig:
    return CoreConfig(
        metadata=VersionMetadata(
            version="1.0.0",
            status=ApprovalStatus.APPROVED,
            approved_by="LeadRiskOfficer",
            approved_at="2026-06-01T00:00:00Z",
            checksum="a" * 64,
            description="Paper-005 Checker Config",
        ),
        strategy=StrategyConfig(
            strategy_id="STRAT-005",
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
            emergency_drawdown_stop_pct=drawdown_stop,
        ),
        universe=UniverseConfig(
            eligible_symbols=("AAPL", "MSFT"),
            min_average_daily_volume_shares=1000,
            min_share_price_dollars=5.0,
        ),
        capital=CapitalAllocationConfig(
            core_allocation_pct=0.80,
            reserve_allocation_pct=0.10,
            black_swan_allocation_pct=0.10,
        ),
    )


receipts = {}
t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

# Check 1: Manual Emergency Kill-Switch (AC-001)
runner1 = PaperTradingRunner(config=make_config(), initial_cash=100_000.0, anchor_timestamp=t0)
runner1.trigger_kill_switch("Checker Manual Kill-Switch Test", t0)
c1_halted = runner1.is_halted
c1_stops = runner1.journal.get_entries_by_type(JournalEventType.EMERGENCY_STOP)
step_res = runner1.step(
    {"AAPL": Bar(timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)}
)
if c1_halted and len(c1_stops) == 1 and len(step_res.orders_submitted) == 0:
    receipts["CHECK1_MANUAL_KILL_SWITCH"] = "VERIFIED"
else:
    receipts["CHECK1_MANUAL_KILL_SWITCH"] = "FAILED"

# Check 2: Emergency Full Liquidation (AC-002)
runner2 = PaperTradingRunner(config=make_config(), initial_cash=100_000.0, anchor_timestamp=t0)
bars2 = generate_bars([100.0, 101.0, 103.0, 105.0, 108.0], t0)
for b in bars2:
    runner2.step({"AAPL": b})
has_pos = "AAPL" in runner2.core_positions
liq_orders = runner2.emergency_liquidate_all(
    timestamp=t0 + timedelta(days=10), reason="Checker Liq"
)
runner2.step(
    {
        "AAPL": Bar(
            timestamp=t0 + timedelta(days=10),
            open=108.0,
            high=109.0,
            low=107.0,
            close=108.0,
            volume=1000.0,
        )
    }
)
pos_cleared = len(runner2.core_positions) == 0
if has_pos and len(liq_orders) >= 1 and pos_cleared and runner2.is_halted:
    receipts["CHECK2_EMERGENCY_FULL_LIQUIDATION"] = "VERIFIED"
else:
    receipts["CHECK2_EMERGENCY_FULL_LIQUIDATION"] = "FAILED"

# Check 3: Automated Drawdown Stop (AC-003)
runner3 = PaperTradingRunner(
    config=make_config(drawdown_stop=0.05), initial_cash=100_000.0, anchor_timestamp=t0
)
bars3 = generate_bars([100.0, 101.0, 103.0, 105.0, 108.0, 70.0], t0)
for b in bars3:
    runner3.step({"AAPL": b})
dd_stops = runner3.journal.get_entries_by_type(JournalEventType.EMERGENCY_STOP)
if runner3.is_halted and len(dd_stops) >= 1:
    receipts["CHECK3_AUTOMATED_DRAWDOWN_STOP"] = "VERIFIED"
else:
    receipts["CHECK3_AUTOMATED_DRAWDOWN_STOP"] = "FAILED"

# Check 4: Corrupt Market Feed Fail-Closed (AC-004)
runner4 = PaperTradingRunner(config=make_config(), initial_cash=100_000.0, anchor_timestamp=t0)
inv_bar = Bar.model_construct(
    timestamp=t0, open=100.0, high=90.0, low=110.0, close=100.0, volume=1000.0
)
c4_inv_failed = False
try:
    runner4.step({"AAPL": inv_bar})
except UnknownPaperInputError:
    c4_inv_failed = True

nan_bar = Bar.model_construct(
    timestamp=t0, open=100.0, high=105.0, low=95.0, close=math.nan, volume=1000.0
)
c4_nan_failed = False
try:
    runner4.step({"AAPL": nan_bar})
except UnknownPaperInputError:
    c4_nan_failed = True

c4_failures = runner4.journal.get_entries_by_type(JournalEventType.FAILURE_RECORDED)
if c4_inv_failed and c4_nan_failed and len(c4_failures) >= 2:
    receipts["CHECK4_CORRUPT_MARKET_FEED_FAIL_CLOSED"] = "VERIFIED"
else:
    receipts["CHECK4_CORRUPT_MARKET_FEED_FAIL_CLOSED"] = "FAILED"

# Check 5: Broker Degradation Gating (AC-005, AC-006, AC-007)
runner5 = PaperTradingRunner(config=make_config(), initial_cash=100_000.0, anchor_timestamp=t0)
ctx_discon = SystemFeedContext(
    is_system_operational=True,
    is_broker_connected=False,
    is_emergency_stop_active=False,
    feed_timestamp=t0,
    evaluation_timestamp=t0,
)
for b in generate_bars([100.0, 101.0, 103.0, 105.0, 108.0], t0):
    runner5.step({"AAPL": b}, feed_context=ctx_discon)
no_orders_discon = len(runner5.broker.get_all_order_snapshots()) == 0
no_pos_discon = len(runner5.core_positions) == 0
no_pending = len(runner5._pending_entries) == 0
if no_orders_discon and no_pos_discon and no_pending:
    receipts["CHECK5_BROKER_DEGRADATION_GATING"] = "VERIFIED"
else:
    receipts["CHECK5_BROKER_DEGRADATION_GATING"] = "FAILED"

# Check 6: Cryptographic Journal Integrity (AC-009)
integrity_rcpt = runner5.journal.verify_integrity(t0 + timedelta(days=10))
if integrity_rcpt.is_valid and runner5.journal.total_entries > 0:
    receipts["CHECK6_CRYPTOGRAPHIC_JOURNAL_INTEGRITY"] = "VERIFIED"
else:
    receipts["CHECK6_CRYPTOGRAPHIC_JOURNAL_INTEGRITY"] = "FAILED"

# Check 7: Post-Halt Config Proposal Lockdown (AC-010)
runner7 = PaperTradingRunner(config=make_config(), initial_cash=100_000.0, anchor_timestamp=t0)
runner7.trigger_kill_switch("Halt for proposal check", t0)
prop = create_production_proposal("P1", "H1", {"breakout_lookback_bars": 3}, "test", t0)
sig = generate_human_approval_signature("T1", "LeadRiskOfficer", prop.proposal_hash, t0)
token = HumanApprovalToken(
    token_id="T1",
    approver_id="LeadRiskOfficer",
    target_proposal_hash=prop.proposal_hash,
    approved_at=t0,
    expires_at=t0 + timedelta(days=1),
    signature=sig,
)
approved_prop = attach_human_approval(prop, token, t0)
c7_blocked = False
try:
    runner7.apply_proposal(approved_prop, current_time=t0)
except EmergencyKillSwitchActiveError:
    c7_blocked = True

if c7_blocked:
    receipts["CHECK7_POST_HALT_CONFIG_LOCKDOWN"] = "VERIFIED"
else:
    receipts["CHECK7_POST_HALT_CONFIG_LOCKDOWN"] = "FAILED"

receipts_file = EVIDENCE_DIR / "checker_paper_005_receipts.json"
receipts_file.write_text(json.dumps(receipts, indent=2))
print("Checker receipts generated:")
print(json.dumps(receipts, indent=2))
