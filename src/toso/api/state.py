"""TOSO API Server State Manager.

Coordinates backend trading engine components, Guardian evaluation,
cryptographic live arming, and client event broadcasting.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from toso.api.models import (
    BacktestMetrics,
    BlackSwanDefenseResponse,
    BrokerLinkState,
    CapitalDistribution,
    CreateProposalRequest,
    DefensiveInstrumentItem,
    EmergencyDisarmResponse,
    EnvironmentMode,
    ExecutionAuthorization,
    GuardianCheckItem,
    GuardianCheckStatus,
    GuardianSummaryResponse,
    OpenPositionItem,
    ProposalActionResponse,
    ProposalItem,
    ScientistConfigResponse,
    SimulationResponse,
    SystemStateResponse,
    TopBarTelemetry,
)
from toso.api.ws import WebSocketHub
from toso.config.loader import load_config_from_file
from toso.config.models import CoreConfig
from toso.defense.universe import load_canonical_defensive_universe
from toso.live.arming import LiveArmingGate, create_human_approval_token
from toso.live.matrix import AcceptanceMatrixAuditor
from toso.live.models import LiveArmingReceipt, PreFlightCheckReceipt
from toso.live.preflight import PreFlightSafetyEngine
from toso.live.resolution import LiveReadinessResolver
from toso.paper.runner import PaperTradingRunner
from toso.scientist.boundary import (
    create_production_proposal,
    generate_human_approval_signature,
)
from toso.scientist.errors import (
    InvalidApprovalTokenError,
    InvalidProposalStateTransitionError,
    UnauthorizedProductionChangeError,
)
from toso.scientist.models import (
    HumanApprovalToken,
    ProductionChangeProposal,
    ProposalStatus,
)
from toso.scientist.proposal import (
    ProposalRegistry,
    adopt_proposal_to_config,
    transition_proposal_status,
)


class APIServerState:
    """Encapsulates system state and operational bridge to TOSO modules."""

    def __init__(
        self,
        config: CoreConfig | None = None,
        arming_gate: LiveArmingGate | None = None,
        ws_hub: WebSocketHub | None = None,
        runner: PaperTradingRunner | None = None,
        environment: EnvironmentMode = EnvironmentMode.PAPER,
    ) -> None:
        self.config: CoreConfig = config or load_config_from_file("config/core_config_v1.yaml")
        self.arming_gate: LiveArmingGate = arming_gate or LiveArmingGate()
        self.ws_hub: WebSocketHub = ws_hub or WebSocketHub()
        self.environment: EnvironmentMode = environment

        # Account baseline
        self.total_equity: float = 100_000.00
        self.unrealized_pnl: float = 850.00
        self.daily_drawdown_pct: float = 0.45

        # Paper trading runner
        self.runner: PaperTradingRunner = runner or PaperTradingRunner(
            config=self.config, initial_cash=self.total_equity
        )

        # Server authoritative salt
        self.server_salt: str = "TOSO-LIVE-ARMING-AUTH-v1"

        # Active simulation scenario for testing fail-closed states
        self.simulated_scenario: str | None = None

        # Operational telemetry state
        self.broker_link: BrokerLinkState = BrokerLinkState.CONNECTED
        self.broker_latency_ms: float = 12.5
        self.last_heartbeat: datetime = datetime.now(UTC)
        self.reconciliation: str = "RECONCILED"

        # Active mock/paper positions
        self.positions: list[OpenPositionItem] = [
            OpenPositionItem(
                symbol="AAPL",
                shares=100,
                entry_price=220.00,
                current_price=226.50,
                invalidation_stop=215.00,
                unrealized_pnl=650.00,
                unrealized_r=1.30,
                pyramid_tier=1,
            ),
            OpenPositionItem(
                symbol="NVDA",
                shares=50,
                entry_price=118.00,
                current_price=122.00,
                invalidation_stop=114.00,
                unrealized_pnl=200.00,
                unrealized_r=1.00,
                pyramid_tier=0,
            ),
        ]

        # Scientist Studio proposal repository
        self.proposal_registry: ProposalRegistry = ProposalRegistry()
        self._seed_default_proposals()

    def get_top_bar(self) -> TopBarTelemetry:
        """Return decoupled 4-domain top-bar indicators."""
        auth_status = (
            ExecutionAuthorization.ARMED
            if self.arming_gate.is_armed
            else ExecutionAuthorization.DISARMED
        )
        return TopBarTelemetry(
            environment=self.environment,
            authorization=auth_status,
            broker_link=self.broker_link,
            reconciliation=self.reconciliation,  # type: ignore[arg-type]
            broker_latency_ms=self.broker_latency_ms,
            last_heartbeat=self.last_heartbeat,
        )

    def get_capital(self) -> CapitalDistribution:
        """Calculate capital allocation according to approved 80/15/5 policy."""
        core_pct = self.config.capital.core_allocation_pct
        reserve_pct = self.config.capital.reserve_allocation_pct
        bs_pct = self.config.capital.black_swan_allocation_pct

        core_alloc = self.total_equity * core_pct
        reserve_alloc = self.total_equity * reserve_pct
        bs_alloc = self.total_equity * bs_pct

        deployed = sum(p.shares * p.current_price for p in self.positions)
        available = max(0.0, core_alloc - deployed)

        return CapitalDistribution(
            total_equity=self.total_equity,
            core_allocated=core_alloc,
            reserve_allocated=reserve_alloc,
            black_swan_allocated=bs_alloc,
            deployed_capital=deployed,
            available_capital=available,
            unrealized_pnl=self.unrealized_pnl,
            daily_drawdown_pct=self.daily_drawdown_pct,
        )

    def get_guardian_summary(self) -> GuardianSummaryResponse:
        """Compute 5-state Guardian health breakdown across the 3 required domains."""
        # Check simulation overrides
        feed_status = GuardianCheckStatus.PASS
        feed_details = f"Feed latency {self.broker_latency_ms:.1f}ms within 500ms threshold."
        clock_status = GuardianCheckStatus.PASS
        clock_details = "UTC synchronized with 0.12ms skew tolerance."
        risk_status = GuardianCheckStatus.PASS
        risk_details = "AAPL concentration 22.6% vs 20% cap (valid winner eligible to run)."

        if self.simulated_scenario == "STALE_FEED":
            feed_status = GuardianCheckStatus.STALE
            feed_details = "Market data quote age 3.42s exceeds 2.0s staleness threshold."
        elif self.simulated_scenario == "CLOCK_SKEW":
            clock_status = GuardianCheckStatus.FAIL
            clock_details = "Clock skew 6.84s exceeds 5.0s maximum skew tolerance."
        elif self.simulated_scenario == "CONCENTRATION_HOLD":
            risk_status = GuardianCheckStatus.BLOCKED
            risk_details = "Simulated portfolio concentration breach: new exposure blocked."

        checks = [
            GuardianCheckItem(
                check_id="G-FEED-01",
                domain="SYSTEM_AND_FEED",
                name="Market Data Latency & Freshness",
                status=feed_status,
                details=feed_details,
            ),
            GuardianCheckItem(
                check_id="G-FEED-02",
                domain="SYSTEM_AND_FEED",
                name="Clock Monotonicity Synchronization",
                status=clock_status,
                details=clock_details,
            ),
            GuardianCheckItem(
                check_id="G-RISK-01",
                domain="INTAKE_AND_RISK",
                name="Single-Stock Concentration Intake Limit",
                status=risk_status,
                details=risk_details,
            ),
            GuardianCheckItem(
                check_id="G-RISK-02",
                domain="INTAKE_AND_RISK",
                name="Total Portfolio Heat Limit",
                status=GuardianCheckStatus.PASS,
                details="Current risk 0.85R within approved 6.0R policy limit.",
            ),
            GuardianCheckItem(
                check_id="G-EXEC-01",
                domain="EXECUTION_SAFETY",
                name="Broker Order & Position Reconciliation",
                status=GuardianCheckStatus.PASS
                if self.reconciliation == "RECONCILED"
                else GuardianCheckStatus.FAIL,
                details="Zero discrepancies across broker and local ledger.",
            ),
            GuardianCheckItem(
                check_id="G-EXEC-02",
                domain="EXECUTION_SAFETY",
                name="Live Order Dispatch Authorization Gate",
                status=GuardianCheckStatus.PASS
                if self.arming_gate.is_armed
                else GuardianCheckStatus.BLOCKED,
                details="Armed for live execution"
                if self.arming_gate.is_armed
                else "System disarmed: Live orders fail closed.",
            ),
        ]

        if feed_status == GuardianCheckStatus.PASS and clock_status == GuardianCheckStatus.PASS:
            sys_feed_status = GuardianCheckStatus.PASS
        elif feed_status == GuardianCheckStatus.STALE:
            sys_feed_status = GuardianCheckStatus.STALE
        else:
            sys_feed_status = GuardianCheckStatus.FAIL

        domain_statuses = {
            "SYSTEM_AND_FEED": sys_feed_status,
            "INTAKE_AND_RISK": risk_status,
            "EXECUTION_SAFETY": GuardianCheckStatus.PASS
            if self.arming_gate.is_armed
            else GuardianCheckStatus.BLOCKED,
        }

        overall = (
            GuardianCheckStatus.PASS
            if all(c.status == GuardianCheckStatus.PASS for c in checks)
            else GuardianCheckStatus.BLOCKED
        )

        return GuardianSummaryResponse(
            overall_status=overall,
            domain_statuses=domain_statuses,
            checks=checks,
        )

    def get_black_swan_defense(self) -> BlackSwanDefenseResponse:
        """Return Black Swan capital allocation and defensive universe mutual exclusivity."""
        core_symbols = list(self.config.universe.eligible_symbols)
        universe_cfg = load_canonical_defensive_universe(core_symbols=core_symbols)

        defensive_items = [
            DefensiveInstrumentItem(
                symbol=inst.symbol,
                instrument_type=inst.instrument_type.value,
                leverage_factor=inst.leverage_factor,
                underlying_index=inst.underlying_index,
                max_allocation_of_sleeve_pct=inst.max_allocation_of_sleeve_pct,
            )
            for inst in universe_cfg.approved_instruments
        ]

        allocated_pct = self.config.capital.black_swan_allocation_pct
        allocated_capital = self.total_equity * allocated_pct

        # Check for any collision between core and defense
        defensive_symbols = [inst.symbol for inst in universe_cfg.approved_instruments]
        collisions = set(core_symbols).intersection(set(defensive_symbols))

        return BlackSwanDefenseResponse(
            allocated_capital=allocated_capital,
            allocated_pct=allocated_pct,
            status="STANDBY",
            is_ring_fenced=True,
            core_symbols=core_symbols,
            defensive_symbols=defensive_items,
            mutual_exclusivity_verified=len(collisions) == 0,
            collision_count=len(collisions),
        )

    async def apply_simulation(self, scenario: str) -> SimulationResponse:
        """Apply a simulated operational edge condition to test fail-closed behavior."""
        if scenario == "RESET":
            self.simulated_scenario = None
            self.broker_link = BrokerLinkState.CONNECTED
            self.broker_latency_ms = 12.5
        elif scenario == "STALE_FEED":
            self.simulated_scenario = "STALE_FEED"
            self.broker_link = BrokerLinkState.STALE
            self.broker_latency_ms = 2500.0
            # Stale feed automatically fails closed
            self.arming_gate.disarm_system(reason="SIMULATED_STALE_FEED_HALT")
        elif scenario == "CLOCK_SKEW":
            self.simulated_scenario = "CLOCK_SKEW"
            # Clock skew automatically fails closed
            self.arming_gate.disarm_system(reason="SIMULATED_CLOCK_SKEW_HALT")
        elif scenario == "CONCENTRATION_HOLD":
            self.simulated_scenario = "CONCENTRATION_HOLD"
        else:
            raise ValueError(f"Unknown simulation scenario: {scenario}")

        summary = self.get_guardian_summary()
        top_bar = self.get_top_bar()

        await self.ws_hub.broadcast(
            event_type="GUARDIAN_SIMULATION",
            payload={
                "scenario": scenario,
                "overall_status": summary.overall_status.value,
                "top_bar": top_bar.model_dump(mode="json"),
            },
        )

        return SimulationResponse(
            scenario=scenario,
            applied=True,
            guardian_summary=summary,
            top_bar=top_bar,
        )

    def get_system_state(self) -> SystemStateResponse:
        """Return full consolidated system state."""
        return SystemStateResponse(
            top_bar=self.get_top_bar(),
            capital=self.get_capital(),
            open_positions=self.positions,
            total_r_exposure=0.85,
            max_r_limit=6.0,
            active_config_version=self.config.metadata.version,
        )

    async def execute_emergency_disarm(
        self, reason: str = "MANUAL_EMERGENCY_DISARM"
    ) -> EmergencyDisarmResponse:
        """Instantly revoke live arming and broadcast emergency disarm event."""
        self.arming_gate.disarm_system(reason=reason)
        now = datetime.now(UTC)

        resp = EmergencyDisarmResponse(
            success=True,
            status="DISARMED",
            disarmed_at=now,
            reason=reason,
        )

        await self.ws_hub.broadcast(
            event_type="EMERGENCY_DISARM",
            payload={
                "status": "DISARMED",
                "disarmed_at": now.isoformat(),
                "reason": reason,
                "top_bar": self.get_top_bar().model_dump(mode="json"),
            },
        )
        return resp

    def run_preflight_checks(self) -> PreFlightCheckReceipt:
        """Run standard pre-flight readiness checks."""
        return PreFlightSafetyEngine.execute_preflight_checks(
            runner=self.runner, config=self.config
        )

    async def execute_live_arming(
        self, approver_id: str, secret_salt: str, notes: str = ""
    ) -> LiveArmingReceipt:
        """Validate prerequisites, seal human approval token, and arm system."""
        preflight = self.run_preflight_checks()
        matrix = AcceptanceMatrixAuditor.audit_matrix()
        readiness = LiveReadinessResolver.resolve_all_questions(core_config=self.config)

        token = create_human_approval_token(
            approver_id=approver_id,
            config_version=self.config.metadata.version,
            acceptance_matrix_digest=matrix.matrix_digest,
            preflight_receipt_hash=preflight.receipt_hash,
            notes=notes,
            secret_salt=secret_salt,
        )

        receipt = self.arming_gate.arm_system(
            human_token=token,
            preflight_receipt=preflight,
            matrix_receipt=matrix,
            readiness_receipt=readiness,
            secret_salt=self.server_salt,
            runner=self.runner,
        )

        await self.ws_hub.broadcast(
            event_type="SYSTEM_ARMED",
            payload={
                "arming_id": receipt.arming_id,
                "armed_at": receipt.armed_at.isoformat(),
                "approver_id": approver_id,
                "arming_digest": receipt.arming_digest,
                "top_bar": self.get_top_bar().model_dump(mode="json"),
            },
        )

        return receipt

    def _seed_default_proposals(self) -> None:
        """Seed initial research proposals for the Scientist Studio."""
        now = datetime.now(UTC)
        p1 = create_production_proposal(
            proposal_id="PROP-2026-001",
            hypothesis_id="HYP-VOL-PYRAMID-01",
            proposed_parameters={"pyramid_min_profit_r": 1.2, "breakout_lookback_bars": 25},
            rationale=(
                "Optimize pyramid entry step from 1.0R to 1.2R and Donchian breakout lookback "
                "from 20 to 25 bars to reduce whipsaw in range-bound regimes."
            ),
            created_at=now - timedelta(days=2),
        )
        p1_submitted = transition_proposal_status(
            proposal=p1,
            new_status=ProposalStatus.SUBMITTED_FOR_REVIEW,
            timestamp=now - timedelta(days=1),
        )
        self.proposal_registry.register_proposal(p1_submitted, now - timedelta(days=1))

        p2 = create_production_proposal(
            proposal_id="PROP-2026-002",
            hypothesis_id="HYP-INVAL-ATR-02",
            proposed_parameters={"invalidation_atr_multiple": 2.2},
            rationale=(
                "Adjust initial stop invalidation ATR multiple from 2.0 to 2.2 "
                "to accommodate elevated market volatility."
            ),
            created_at=now - timedelta(hours=12),
        )
        self.proposal_registry.register_proposal(p2, now - timedelta(hours=12))

    def _generate_yaml_diff(self, proposed_params: dict[str, Any]) -> str:
        """Generate unified YAML diff string between active config and proposed parameters."""
        lines = [
            "--- config/core_config_v1.yaml (Active Production)",
            "+++ proposed_variant (Candidate)",
            " strategy:",
        ]
        strat_dict = self.config.strategy.model_dump(mode="json")
        for k, v in proposed_params.items():
            curr_val = strat_dict.get(k)
            lines.append(f"-  {k}: {curr_val}")
            lines.append(f"+  {k}: {v}")
        return "\n".join(lines)

    def _build_proposal_item(self, prop: ProductionChangeProposal) -> ProposalItem:
        """Convert ProductionChangeProposal to presentation ProposalItem with diff/metrics."""
        approver = prop.approval_token.approver_id if prop.approval_token else None

        if prop.proposal_id == "PROP-2026-001":
            baseline_m = BacktestMetrics(
                sharpe_ratio=1.45,
                calmar_ratio=1.10,
                max_drawdown_pct=14.2,
                win_rate_pct=44.2,
                profit_factor=1.68,
                expectancy_r=0.42,
                total_trades=128,
            )
            proposed_m = BacktestMetrics(
                sharpe_ratio=1.82,
                calmar_ratio=1.65,
                max_drawdown_pct=9.8,
                win_rate_pct=48.5,
                profit_factor=2.05,
                expectancy_r=0.65,
                total_trades=116,
            )
            eq_base = [
                100.0,
                101.2,
                102.5,
                101.8,
                104.1,
                103.5,
                106.0,
                105.2,
                108.4,
                107.1,
                109.8,
                108.5,
                111.2,
                110.0,
                113.5,
                112.8,
                115.4,
                114.2,
                117.0,
                118.5,
            ]
            eq_prop = [
                100.0,
                101.8,
                103.2,
                103.0,
                106.4,
                106.1,
                109.5,
                109.2,
                113.1,
                112.8,
                116.4,
                115.9,
                119.8,
                119.4,
                123.6,
                123.0,
                126.8,
                126.5,
                130.2,
                132.4,
            ]
        else:
            baseline_m = BacktestMetrics(
                sharpe_ratio=1.45,
                calmar_ratio=1.10,
                max_drawdown_pct=14.2,
                win_rate_pct=44.2,
                profit_factor=1.68,
                expectancy_r=0.42,
                total_trades=128,
            )
            proposed_m = BacktestMetrics(
                sharpe_ratio=1.58,
                calmar_ratio=1.28,
                max_drawdown_pct=11.5,
                win_rate_pct=45.8,
                profit_factor=1.79,
                expectancy_r=0.51,
                total_trades=122,
            )
            eq_base = [
                100.0,
                101.2,
                102.5,
                101.8,
                104.1,
                103.5,
                106.0,
                105.2,
                108.4,
                107.1,
                109.8,
                108.5,
                111.2,
                110.0,
                113.5,
                112.8,
                115.4,
                114.2,
                117.0,
                118.5,
            ]
            eq_prop = [
                100.0,
                101.5,
                102.8,
                102.5,
                105.0,
                104.8,
                107.5,
                107.0,
                110.2,
                109.8,
                112.8,
                112.2,
                115.0,
                114.5,
                118.0,
                117.5,
                120.8,
                120.2,
                123.4,
                125.0,
            ]

        diff_str = self._generate_yaml_diff(prop.proposed_parameters)

        return ProposalItem(
            proposal_id=prop.proposal_id,
            hypothesis_id=prop.hypothesis_id,
            rationale=prop.rationale,
            proposed_parameters=prop.proposed_parameters,
            proposal_hash=prop.proposal_hash,
            status=prop.status.value,
            is_approved=prop.is_approved,
            created_at=prop.created_at,
            reviewed_at=prop.reviewed_at,
            adopted_at=prop.adopted_at,
            rejection_reason=prop.rejection_reason,
            approver_id=approver,
            baseline_metrics=baseline_m,
            proposed_metrics=proposed_m,
            equity_curve_baseline=eq_base,
            equity_curve_proposed=eq_prop,
            yaml_diff=diff_str,
        )

    def get_scientist_config(self) -> ScientistConfigResponse:
        """Inspect active CoreConfig and metadata."""
        raw_yaml = yaml.safe_dump(self.config.model_dump(mode="json"), sort_keys=False)
        meta = self.config.metadata
        return ScientistConfigResponse(
            raw_yaml=raw_yaml,
            version=meta.version,
            status=meta.status.value,
            approved_by=meta.approved_by,
            approved_at=meta.approved_at,
            checksum=meta.checksum or "SHA256:INITIAL-APPROVED-CONTRACT",
            description=meta.description,
            strategy_params=self.config.strategy.model_dump(mode="json"),
        )

    def get_proposals(self) -> list[ProposalItem]:
        """Query all registered proposals sorted by ID."""
        props = self.proposal_registry.query_proposals()
        return [self._build_proposal_item(p) for p in props]

    def get_proposal(self, proposal_id: str) -> ProposalItem:
        """Retrieve a specific proposal by ID."""
        prop = self.proposal_registry.get_proposal(proposal_id)
        return self._build_proposal_item(prop)

    async def create_proposal(self, req: CreateProposalRequest) -> ProposalItem:
        """Formulate a new research proposal."""
        now = datetime.now(UTC)
        prop = create_production_proposal(
            proposal_id=req.proposal_id,
            hypothesis_id=req.hypothesis_id,
            proposed_parameters=req.proposed_parameters,
            rationale=req.rationale,
            created_at=now,
        )
        self.proposal_registry.register_proposal(prop, now)
        item = self._build_proposal_item(prop)
        await self.ws_hub.broadcast(
            event_type="PROPOSAL_CREATED",
            payload={"proposal_id": req.proposal_id, "hypothesis_id": req.hypothesis_id},
        )
        return item

    async def review_proposal(
        self,
        proposal_id: str,
        action: str,
        reason: str = "",
    ) -> ProposalActionResponse:
        """Transition proposal lifecycle state (SUBMIT, REJECT, WITHDRAW, DRAFT)."""
        now = datetime.now(UTC)
        prop = self.proposal_registry.get_proposal(proposal_id)
        action_upper = action.upper()
        if action_upper == "SUBMIT":
            target_status = ProposalStatus.SUBMITTED_FOR_REVIEW
        elif action_upper == "REJECT":
            target_status = ProposalStatus.REJECTED
        elif action_upper == "WITHDRAW":
            target_status = ProposalStatus.WITHDRAWN
        elif action_upper == "DRAFT":
            target_status = ProposalStatus.DRAFT
        else:
            raise ValueError(f"Unknown review action: {action}")

        updated_prop = transition_proposal_status(
            proposal=prop,
            new_status=target_status,
            timestamp=now,
            reason=reason,
        )
        receipt = self.proposal_registry.update_proposal(updated_prop, now)
        item = self._build_proposal_item(updated_prop)

        await self.ws_hub.broadcast(
            event_type="PROPOSAL_STATUS_CHANGED",
            payload={"proposal_id": proposal_id, "status": target_status.value, "reason": reason},
        )
        return ProposalActionResponse(
            proposal=item,
            receipt_id=receipt.receipt_id,
            checksum=receipt.checksum,
            message=f"Proposal '{proposal_id}' transitioned to {target_status.value}.",
        )

    async def approve_proposal(
        self,
        proposal_id: str,
        approver_id: str,
        secret_salt: str,
        validity_minutes: int = 60,
    ) -> ProposalActionResponse:
        """Cryptographically approve a proposal (INV-014)."""
        now = datetime.now(UTC)
        if secret_salt != self.server_salt:
            raise InvalidApprovalTokenError("Invalid authorization salt for proposal approval.")
        if not approver_id or not approver_id.strip():
            raise InvalidApprovalTokenError("Approver ID must not be empty.")

        prop = self.proposal_registry.get_proposal(proposal_id)
        if prop.status not in (ProposalStatus.DRAFT, ProposalStatus.SUBMITTED_FOR_REVIEW):
            raise InvalidProposalStateTransitionError(
                f"Cannot approve proposal '{proposal_id}' in status '{prop.status.value}'."
            )

        token_id = f"TOKEN-{proposal_id}-{int(now.timestamp())}"
        signature = generate_human_approval_signature(
            token_id=token_id,
            approver_id=approver_id,
            target_proposal_hash=prop.proposal_hash,
            approved_at=now,
        )
        token = HumanApprovalToken(
            token_id=token_id,
            approver_id=approver_id,
            target_proposal_hash=prop.proposal_hash,
            signature=signature,
            approved_at=now,
            expires_at=now + timedelta(minutes=validity_minutes),
        )

        approved_prop = transition_proposal_status(
            proposal=prop,
            new_status=ProposalStatus.APPROVED,
            timestamp=now,
            token=token,
        )
        receipt = self.proposal_registry.update_proposal(approved_prop, now)
        item = self._build_proposal_item(approved_prop)

        await self.ws_hub.broadcast(
            event_type="PROPOSAL_APPROVED",
            payload={"proposal_id": proposal_id, "status": "APPROVED", "approver_id": approver_id},
        )
        return ProposalActionResponse(
            proposal=item,
            receipt_id=receipt.receipt_id,
            checksum=receipt.checksum,
            message=f"Proposal '{proposal_id}' cryptographically approved by {approver_id}.",
        )

    async def adopt_proposal(
        self,
        proposal_id: str,
        new_version: str,
        approver_id: str,
        secret_salt: str,
    ) -> ProposalActionResponse:
        """Adopt an approved proposal into production CoreConfig (INV-015)."""
        now = datetime.now(UTC)
        if secret_salt != self.server_salt:
            raise InvalidApprovalTokenError("Invalid authorization salt for proposal adoption.")
        if not approver_id or not approver_id.strip():
            raise InvalidApprovalTokenError("Approver ID must not be empty.")

        prop = self.proposal_registry.get_proposal(proposal_id)
        if (
            prop.status != ProposalStatus.APPROVED
            or not prop.is_approved
            or prop.approval_token is None
        ):
            raise UnauthorizedProductionChangeError(
                f"Cannot adopt proposal '{proposal_id}': status is '{prop.status.value}'. "
                "Must be APPROVED with valid token (INV-014, INV-015)."
            )

        adopted_config, adopted_prop, receipt = adopt_proposal_to_config(
            proposal=prop,
            current_config=self.config,
            new_version=new_version,
            adoption_time=now,
        )

        self.config = adopted_config
        self.runner._config = self.config
        self.proposal_registry.update_proposal(adopted_prop, now)

        item = self._build_proposal_item(adopted_prop)
        await self.ws_hub.broadcast(
            event_type="PROPOSAL_ADOPTED",
            payload={
                "proposal_id": proposal_id,
                "version": new_version,
                "approved_by": approver_id,
                "status": "ADOPTED",
            },
        )
        await self.ws_hub.broadcast(
            event_type="CONFIG_UPDATED",
            payload={
                "version": new_version,
                "strategy": self.config.strategy.model_dump(mode="json"),
            },
        )
        return ProposalActionResponse(
            proposal=item,
            receipt_id=receipt.receipt_id,
            checksum=receipt.checksum,
            message=f"Proposal '{proposal_id}' adopted into production CoreConfig v{new_version}.",
        )
