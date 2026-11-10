"""TOSO End-to-End Core Trader & Guardian Paper Simulation Runner.

Implements Story PAPER-002 (Phase 6 Paper Validation):
- Complete end-to-end deterministic pipeline execution (FR-001 - FR-015, INV-001 - INV-008).
- Guardian Execution Gate verification (submit_through_gate) on every entry & pyramid.
- Strict invalidation enforcement without discretionary overrides or hold delays.
- Winner-only pyramiding; strict prevention of averaging down on losing trades.
- Winner continuation honoring trend following without premature profit-taking.
- Multi-step authoritative reconciliation against Phase 3 broker models.
- Capital sleeve isolation and emergency peak-to-trough drawdown halt stops.
- Pure clock anchoring and SHA-256 sealed simulation audit summaries.
"""

import hashlib
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from toso.broker.account import reconcile_account_state
from toso.broker.idempotency import generate_idempotent_order_id
from toso.broker.models import (
    LocalAccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from toso.broker.positions import reconcile_positions
from toso.config.errors import UnapprovedConfigurationError
from toso.config.models import ApprovalStatus, CoreConfig
from toso.continuation.evaluator import evaluate_winner_continuation
from toso.defense.capital import (
    calculate_black_swan_capital,
    enforce_black_swan_intake_safety,
    evaluate_black_swan_capital_intake,
)
from toso.defense.errors import (
    UnknownDefenseInputError,
)
from toso.defense.exit import evaluate_defensive_position_exit
from toso.defense.models import (
    DefensiveExitAction,
    DefensiveInstrumentSpec,
    DefensiveInstrumentType,
    DefensivePosition,
    DefensiveTriggerSignal,
    DefensiveTriggerType,
    DefensiveUniverseConfig,
)
from toso.defense.sizing import (
    enforce_defensive_position_sizing,
)
from toso.defense.universe import enforce_defensive_instrument_eligibility
from toso.entry.evaluator import create_entry_from_signal
from toso.entry.models import CoreEntryOrder, InvalidationDefinition
from toso.exit.evaluator import evaluate_position_exit
from toso.exit.models import CorePosition, PositionStatus
from toso.guardian.gate import submit_through_gate
from toso.guardian.models import (
    CapitalSleeve,
    PortfolioStateSnapshot,
    PositionRiskSnapshot,
    ProposedExposure,
    SystemFeedContext,
)
from toso.paper.broker import PaperBroker
from toso.paper.errors import EmergencyKillSwitchActiveError, UnknownPaperInputError
from toso.paper.models import (
    PaperFill,
    PaperSimulationStepResult,
    PaperSimulationSummary,
    PaperTradeRecord,
)
from toso.pyramiding.errors import PyramidingError
from toso.pyramiding.evaluator import create_pyramid_order, evaluate_pyramid_eligibility
from toso.scientist.boundary import ScientistExecutionGuard
from toso.scientist.journal import TradeJournal
from toso.scientist.models import (
    JournalEventType,
    ProductionChangeProposal,
    ScientistRole,
)
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.models import Bar, BarSeries, SignalType
from toso.sizing.calculator import calculate_position_size
from toso.sizing.errors import SizingError


def _validate_approved_config(config: CoreConfig) -> None:
    """Ensure configuration is approved and versioned (FR-001, INV-008)."""
    if not isinstance(config, CoreConfig):
        raise UnknownPaperInputError(f"config must be CoreConfig, got {type(config)}")
    if config.metadata.status != ApprovalStatus.APPROVED:
        raise UnapprovedConfigurationError(
            f"Only APPROVED configuration can run simulation, got {config.metadata.status}"
        )


def compute_simulation_checksum(
    initial_equity: float,
    final_equity: float,
    total_trades: int,
    completed_trades: Sequence[PaperTradeRecord],
) -> str:
    """Compute deterministic SHA-256 seal for simulation summary (INV-001)."""
    trade_str = ";".join(
        f"{t.trade_id}:{t.symbol}:{t.pnl:.2f}:{t.exit_timestamp.isoformat()}"
        for t in completed_trades
    )
    payload = f"{initial_equity:.4f}:{final_equity:.4f}:{total_trades}:{trade_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PaperTradingRunner:
    """Deterministic orchestrator for Core Trader & Guardian paper simulation (Phase 6)."""

    def __init__(
        self,
        config: CoreConfig,
        initial_cash: float = 100_000.0,
        broker: PaperBroker | None = None,
        anchor_timestamp: datetime | None = None,
        defensive_universe: DefensiveUniverseConfig | None = None,
        journal: TradeJournal | None = None,
    ) -> None:
        """Initialize simulation runner with approved config, simulated broker, and journal."""
        _validate_approved_config(config)
        if (
            not isinstance(initial_cash, (int, float))
            or initial_cash <= 0
            or math.isnan(initial_cash)
        ):
            raise UnknownPaperInputError(
                f"initial_cash must be positive finite float, got {initial_cash}"
            )

        self._config = config
        self._signal_generator = DeterministicSignalGenerator(config.strategy)
        self._initial_cash = float(initial_cash)
        self._peak_equity = float(initial_cash)
        self._is_halted = False
        self._step_index = 0
        self._defensive_universe = defensive_universe

        init_ts = anchor_timestamp or datetime(2026, 1, 1, 0, 0, 0)
        self._journal = journal or TradeJournal()
        self._journal.record_event(
            event_type=JournalEventType.CONFIGURATION_LOADED,
            payload={
                "version": config.metadata.version,
                "status": config.metadata.status.value,
                "strategy_id": config.strategy.strategy_id,
                "initial_cash": float(initial_cash),
                "core_allocation_pct": config.capital.core_allocation_pct,
                "reserve_allocation_pct": config.capital.reserve_allocation_pct,
                "black_swan_allocation_pct": config.capital.black_swan_allocation_pct,
            },
            timestamp=init_ts,
        )

        self._broker = broker or PaperBroker(
            account_id="PAPER-CORE-001",
            initial_cash=initial_cash,
            slippage_bps=config.risk.slippage_buffer_pct * 10000.0,
            anchor_timestamp=anchor_timestamp,
            defensive_universe=defensive_universe,
        )
        if (
            broker is not None
            and defensive_universe is not None
            and broker._defensive_universe is None
        ):
            broker._defensive_universe = defensive_universe

        self._bar_history: dict[str, list[Bar]] = {}
        self._core_positions: dict[str, CorePosition] = {}
        self._defensive_positions: dict[str, DefensivePosition] = {}
        self._completed_trades: list[PaperTradeRecord] = []
        self._step_results: list[PaperSimulationStepResult] = []
        self._pending_entries: dict[str, CoreEntryOrder] = {}
        self._pending_defensive_entries: dict[str, DefensiveInstrumentSpec] = {}
        self._pending_defensive_exit_reasons: dict[str, str] = {}
        self._last_timestamp = anchor_timestamp or datetime(2026, 1, 1, 0, 0, 0)

    @property
    def config(self) -> CoreConfig:
        """Return the running CoreConfig."""
        return self._config

    @property
    def journal(self) -> TradeJournal:
        """Return the authoritative append-only trade journal."""
        return self._journal

    @property
    def broker(self) -> PaperBroker:
        """Return the internal PaperBroker."""
        return self._broker

    @property
    def is_halted(self) -> bool:
        """Return whether simulation is in HALTED state."""
        return self._is_halted

    @property
    def core_positions(self) -> dict[str, CorePosition]:
        """Return active tracked CorePosition mapping."""
        return dict(self._core_positions)

    @property
    def defensive_positions(self) -> dict[str, DefensivePosition]:
        """Return active tracked DefensivePosition mapping."""
        return dict(self._defensive_positions)

    @property
    def defensive_universe(self) -> DefensiveUniverseConfig | None:
        """Return configured DefensiveUniverseConfig if active."""
        return self._defensive_universe

    @property
    def completed_trades(self) -> tuple[PaperTradeRecord, ...]:
        """Return history of closed round-trip trades."""
        return tuple(self._completed_trades)

    def _select_defensive_instrument_for_trigger(
        self, trigger: DefensiveTriggerSignal
    ) -> DefensiveInstrumentSpec | None:
        """Deterministically select an approved defensive instrument for a crisis trigger."""
        if not self._defensive_universe:
            return None
        type_preference = {
            DefensiveTriggerType.VOLATILITY_SPIKE: (
                DefensiveInstrumentType.VOLATILITY_ETP,
                DefensiveInstrumentType.INVERSE_INDEX_ETF,
            ),
            DefensiveTriggerType.BENCHMARK_BREAKDOWN: (
                DefensiveInstrumentType.INVERSE_INDEX_ETF,
                DefensiveInstrumentType.LEVERAGED_INVERSE_ETF,
            ),
            DefensiveTriggerType.PORTFOLIO_DRAWDOWN_WARNING: (
                DefensiveInstrumentType.CASH_EQUIVALENT,
                DefensiveInstrumentType.INVERSE_INDEX_ETF,
            ),
            DefensiveTriggerType.MANUAL_OVERRIDE: (
                DefensiveInstrumentType.INVERSE_INDEX_ETF,
                DefensiveInstrumentType.VOLATILITY_ETP,
            ),
        }
        preferred = type_preference.get(trigger.trigger_type, ())
        for pref in preferred:
            for spec in self._defensive_universe.approved_instruments:
                if spec.instrument_type == pref and spec.symbol not in self._defensive_positions:
                    return spec
        for spec in self._defensive_universe.approved_instruments:
            if spec.symbol not in self._defensive_positions:
                return spec
        return None

    def _get_defensive_spec(self, symbol: str) -> DefensiveInstrumentSpec | None:
        """Find approved DefensiveInstrumentSpec by ticker symbol."""
        if not self._defensive_universe:
            return None
        for spec in self._defensive_universe.approved_instruments:
            if spec.symbol == symbol:
                return spec
        return None

    def get_portfolio_snapshot(self, timestamp: datetime | None = None) -> PortfolioStateSnapshot:
        """Return authoritative PortfolioStateSnapshot for the current simulation state."""
        ts = timestamp or self._last_timestamp
        return self._build_portfolio_snapshot(ts)

    def apply_proposal(
        self,
        proposal: ProductionChangeProposal,
        current_time: datetime,
    ) -> CoreConfig:
        """Apply an approved Scientist proposal to paper runner config (INV-014).

        Guarantees:
        - Autonomous changes without valid HumanApprovalToken are strictly blocked (INV-014).
        - Verifies approval signature, token validity window, and proposal hash match.
        - Updates active strategy configuration and deterministic signal generator.
        - Logs CONFIGURATION_LOADED event to append-only journal.
        """
        if self._is_halted:
            raise EmergencyKillSwitchActiveError(
                "Cannot apply configuration proposal while simulation is halted by "
                "emergency kill switch."
            )

        approved_params = ScientistExecutionGuard.apply_production_change(
            proposal=proposal, current_time=current_time
        )
        strat_dict = self._config.strategy.model_dump()
        for k, v in approved_params.items():
            if k in strat_dict:
                strat_dict[k] = v
        new_strategy = self._config.strategy.__class__(**strat_dict)
        new_config = CoreConfig(
            metadata=self._config.metadata,
            strategy=new_strategy,
            risk=self._config.risk,
            capital=self._config.capital,
            universe=self._config.universe,
        )
        self._config = new_config
        self._signal_generator = DeterministicSignalGenerator(new_strategy)
        self._journal.record_event(
            event_type=JournalEventType.CONFIGURATION_LOADED,
            payload={
                "action": "PROPOSAL_APPLIED",
                "proposal_id": proposal.proposal_id,
                "approved_parameters": approved_params,
            },
            timestamp=current_time,
        )
        return self._config

    def submit_as_scientist(
        self,
        action: str,
        timestamp: datetime,
        caller_role: ScientistRole = ScientistRole.RESEARCHER,
    ) -> None:
        """Attempting to execute from the Scientist boundary is blocked (INV-014)."""
        ScientistExecutionGuard.intercept_order_submission(
            caller_role=caller_role,
            action=action,
            timestamp=timestamp,
        )

    def trigger_kill_switch(self, reason: str, timestamp: datetime) -> None:
        """Trigger an immediate manual emergency kill-switch halt (FR-015).

        - Sets simulation runner state to halted.
        - Records EMERGENCY_STOP event to append-only trade journal.
        - Cancels all active, non-terminal resting orders on paper broker.
        """
        if not reason or not isinstance(reason, str):
            raise UnknownPaperInputError("Kill switch reason must be a non-empty string.")
        if not isinstance(timestamp, datetime):
            raise UnknownPaperInputError("Kill switch timestamp must be a valid datetime.")

        self._is_halted = True
        self._journal.record_event(
            event_type=JournalEventType.EMERGENCY_STOP,
            payload={
                "trigger": "MANUAL_KILL_SWITCH",
                "reason": reason,
                "current_equity": round(self._broker.total_equity, 4),
                "peak_equity": round(self._peak_equity, 4),
            },
            timestamp=timestamp,
        )
        for o in self._broker.get_all_order_snapshots():
            if not o.is_terminal:
                self._broker.cancel_order(o.order_id, timestamp=timestamp)

    def emergency_liquidate_all(
        self, timestamp: datetime, reason: str = "EMERGENCY_LIQUIDATION"
    ) -> list[str]:
        """Liquidate all open Core and Defensive positions immediately (FR-015).

        - Automatically triggers emergency kill-switch halt if not already halted.
        - Submits unconditional MARKET SELL orders for 100% of open positions.
        - Returns list of submitted liquidation order IDs.
        """
        if not isinstance(timestamp, datetime):
            raise UnknownPaperInputError("Liquidation timestamp must be a valid datetime.")

        if not self._is_halted:
            self.trigger_kill_switch(reason=reason, timestamp=timestamp)

        liquidation_orders: list[str] = []

        # Liquidate all open Core positions
        for sym in list(self._core_positions.keys()):
            broker_pos = self._broker.get_position(sym)
            if broker_pos and broker_pos.shares > 0:
                intent = OrderIntent(
                    symbol=sym,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=broker_pos.shares,
                    signal_id=f"EMERGENCY-LIQ-{sym}-{int(timestamp.timestamp())}",
                    intent_timestamp=timestamp,
                )
                submitted_o, _ = self._broker.submit_order(intent)
                liquidation_orders.append(submitted_o.order_id)
                self._journal.record_event(
                    event_type=JournalEventType.EXECUTION_SUBMITTED,
                    payload={
                        "order_id": submitted_o.order_id,
                        "symbol": sym,
                        "side": OrderSide.SELL.value,
                        "quantity": broker_pos.shares,
                        "order_type": OrderType.MARKET.value,
                        "reason": f"EMERGENCY_LIQUIDATION: {reason}",
                    },
                    timestamp=timestamp,
                )

        # Liquidate all open Defensive positions
        for sym in list(self._defensive_positions.keys()):
            broker_pos = self._broker.get_position(sym)
            if broker_pos and broker_pos.shares > 0:
                intent = OrderIntent(
                    symbol=sym,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=broker_pos.shares,
                    signal_id=f"EMERGENCY-LIQ-DEF-{sym}-{int(timestamp.timestamp())}",
                    intent_timestamp=timestamp,
                    target_sleeve=CapitalSleeve.BLACK_SWAN,
                )
                self._pending_defensive_exit_reasons[sym] = "EMERGENCY_LIQUIDATION"
                submitted_o, _ = self._broker.submit_order(intent)
                liquidation_orders.append(submitted_o.order_id)
                self._journal.record_event(
                    event_type=JournalEventType.EXECUTION_SUBMITTED,
                    payload={
                        "order_id": submitted_o.order_id,
                        "symbol": sym,
                        "side": OrderSide.SELL.value,
                        "quantity": broker_pos.shares,
                        "order_type": OrderType.MARKET.value,
                        "reason": f"EMERGENCY_LIQUIDATION: {reason}",
                    },
                    timestamp=timestamp,
                )

        return liquidation_orders

    @classmethod
    def reconstruct_from_journal(cls, journal: TradeJournal) -> dict[str, Any]:
        """Deterministically reconstruct event statistics and state from journal (AC-003)."""
        entries = journal.get_entries()
        events_by_type: dict[str, int] = {}
        cash: float = 0.0
        open_positions: dict[str, dict[str, Any]] = {}
        completed_trades: list[dict[str, Any]] = []
        is_halted: bool = False
        last_prices: dict[str, float] = {}

        for entry in entries:
            t = entry.event_type.value
            events_by_type[t] = events_by_type.get(t, 0) + 1
            payload = entry.payload

            if entry.event_type == JournalEventType.CONFIGURATION_LOADED:
                if "initial_cash" in payload:
                    cash = float(payload["initial_cash"])

            elif entry.event_type == JournalEventType.SIGNAL_GENERATED:
                if "bar_close" in payload and "symbol" in payload:
                    last_prices[payload["symbol"]] = float(payload["bar_close"])

            elif entry.event_type == JournalEventType.EMERGENCY_STOP:
                is_halted = True

            elif entry.event_type == JournalEventType.BROKER_FILL:
                qty = float(payload.get("quantity", 0))
                price = float(payload.get("price", 0.0))
                side = payload.get("side")
                sym = payload.get("symbol", "")
                last_prices[sym] = price
                if side == OrderSide.BUY.value:
                    cash -= qty * price
                elif side == OrderSide.SELL.value:
                    cash += qty * price

            elif entry.event_type == JournalEventType.POSITION_CHANGED:
                action = payload.get("action")
                sym = payload.get("symbol", "")
                if action in ("POSITION_OPENED", "DEFENSIVE_POSITION_OPENED"):
                    open_positions[sym] = {
                        "position_id": payload.get("position_id"),
                        "symbol": sym,
                        "shares": float(payload.get("shares", 0)),
                        "entry_price": float(payload.get("entry_price", 0.0)),
                        "sleeve": payload.get("sleeve", "CORE"),
                    }
                elif action == "PYRAMID_POSITION_ADDED":
                    if sym in open_positions:
                        open_positions[sym]["shares"] = float(payload.get("total_shares", 0))
                        open_positions[sym]["entry_price"] = float(
                            payload.get("new_average_entry_price", 0.0)
                        )
                elif action in ("POSITION_CLOSED", "DEFENSIVE_POSITION_CLOSED"):
                    if sym in open_positions:
                        pos = open_positions.pop(sym)
                        completed_trades.append(
                            {
                                "position_id": pos["position_id"],
                                "symbol": sym,
                                "shares": float(payload.get("shares", pos["shares"])),
                                "entry_price": pos["entry_price"],
                                "exit_price": float(payload.get("exit_price", 0.0)),
                                "pnl": float(payload.get("pnl", 0.0)),
                                "pnl_pct": float(payload.get("pnl_pct", 0.0)),
                                "reason": payload.get("reason", ""),
                            }
                        )

        open_mkt_val = sum(
            pos["shares"] * last_prices.get(pos["symbol"], pos["entry_price"])
            for pos in open_positions.values()
        )
        total_equity = cash + open_mkt_val

        return {
            "total_entries": len(entries),
            "event_counts": events_by_type,
            "cash_balance": round(cash, 4),
            "total_equity": round(total_equity, 4),
            "open_positions": open_positions,
            "completed_trades": completed_trades,
            "is_halted": is_halted,
            "latest_sequence_number": entries[-1].sequence_number if entries else -1,
            "latest_entry_hash": entries[-1].entry_hash if entries else "GENESIS",
        }

    def _build_portfolio_snapshot(self, timestamp: datetime) -> PortfolioStateSnapshot:
        """Construct authoritative PortfolioStateSnapshot for Guardian evaluation."""
        pos_snapshots: list[PositionRiskSnapshot] = []
        for sym, cpos in sorted(self._core_positions.items()):
            broker_pos = self._broker.get_position(sym)
            if broker_pos and broker_pos.shares > 0:
                cur_price = broker_pos.current_price
                risk_d = max(0.0, broker_pos.shares * (cur_price - cpos.invalidation.price))
                pos_snapshots.append(
                    PositionRiskSnapshot(
                        symbol=sym,
                        shares=broker_pos.shares,
                        current_price=cur_price,
                        stop_loss_price=cpos.invalidation.price,
                        open_risk_dollars=round(risk_d, 4),
                        market_value=round(broker_pos.market_value, 4),
                        sleeve=CapitalSleeve.CORE,
                    )
                )

        for sym, dpos in sorted(self._defensive_positions.items()):
            broker_pos = self._broker.get_position(sym)
            if broker_pos and broker_pos.shares > 0:
                cur_price = broker_pos.current_price
                pos_snapshots.append(
                    PositionRiskSnapshot(
                        symbol=sym,
                        shares=broker_pos.shares,
                        current_price=cur_price,
                        stop_loss_price=round(dpos.entry_price * 0.5, 4),
                        open_risk_dollars=0.0,
                        market_value=round(broker_pos.market_value, 4),
                        sleeve=CapitalSleeve.BLACK_SWAN,
                    )
                )

        return PortfolioStateSnapshot(
            portfolio_equity=round(self._broker.total_equity, 4),
            peak_portfolio_equity=round(self._peak_equity, 4),
            cash_balance=round(self._broker.cash_balance, 4),
            positions=tuple(pos_snapshots),
            timestamp=timestamp,
        )

    def step(
        self,
        bars: dict[str, Bar],
        feed_context: SystemFeedContext | None = None,
        defensive_triggers: Sequence[DefensiveTriggerSignal] | None = None,
        normalization_signals: Sequence[str] | None = None,
    ) -> PaperSimulationStepResult:
        """Execute a single discrete simulation bar step across universe symbols."""
        if not isinstance(bars, dict) or not bars:
            raise UnknownPaperInputError("bars must be a non-empty dictionary of symbol to Bar.")

        # Determine step timestamp and validate chronological ordering
        first_bar = next(iter(bars.values()))
        if not isinstance(first_bar, Bar):
            raise UnknownPaperInputError("bars values must be Bar instances.")
        step_ts = first_bar.timestamp

        if step_ts < self._last_timestamp:
            self._journal.record_event(
                event_type=JournalEventType.FAILURE_RECORDED,
                payload={
                    "error": "NonChronologicalStepTimestamp",
                    "step_ts": step_ts.isoformat(),
                    "last_timestamp": self._last_timestamp.isoformat(),
                },
                timestamp=self._last_timestamp,
            )
            raise UnknownPaperInputError(
                f"Non-chronological step timestamp: {step_ts} < {self._last_timestamp}"
            )
        self._last_timestamp = step_ts

        # Validate all bars for price inversion, negative, or NaN/Inf
        for sym, bar in sorted(bars.items()):
            if not isinstance(bar, Bar):
                raise UnknownPaperInputError(f"Bar for {sym} must be a Bar instance.")
            for p in (bar.open, bar.high, bar.low, bar.close):
                if math.isnan(p) or math.isinf(p) or p <= 0:
                    self._journal.record_event(
                        event_type=JournalEventType.FAILURE_RECORDED,
                        payload={"error": "CorruptBarPrice", "symbol": sym, "value": str(p)},
                        timestamp=step_ts,
                    )
                    raise UnknownPaperInputError(f"Corrupt or non-positive price for {sym}: {p}")
            if bar.high < bar.low:
                self._journal.record_event(
                    event_type=JournalEventType.FAILURE_RECORDED,
                    payload={
                        "error": "InvertedBarPrices",
                        "symbol": sym,
                        "high": bar.high,
                        "low": bar.low,
                    },
                    timestamp=step_ts,
                )
                raise UnknownPaperInputError(
                    f"Inverted bar prices for {sym}: high {bar.high} < low {bar.low}"
                )

        # Check feed context for emergency stop circuit breaker
        if (
            feed_context is not None
            and feed_context.is_emergency_stop_active
            and not self._is_halted
        ):
            self.trigger_kill_switch(
                reason="SystemFeedContext emergency stop active",
                timestamp=step_ts,
            )

        signals_map: dict[str, str] = {}
        orders_submitted: list[str] = []
        all_fills: list[PaperFill] = []

        # 1. Feed Ingestion & Broker Fill Execution
        for sym, bar in sorted(bars.items()):
            if not isinstance(bar, Bar):
                raise UnknownPaperInputError(f"Bar for {sym} must be a Bar instance.")
            self._bar_history.setdefault(sym, []).append(bar)

            # Broker executes resting orders for symbol
            fills, _ = self._broker.process_bar(sym, bar)
            for f in fills:
                all_fills.append(f)
                self._handle_fill(f, bar)

        # Update mark-to-market prices for active defensive positions
        for sym, dpos in list(self._defensive_positions.items()):
            bar = bars.get(sym)
            if bar:
                self._defensive_positions[sym] = DefensivePosition(
                    position_id=dpos.position_id,
                    symbol=dpos.symbol,
                    shares=dpos.shares,
                    entry_price=dpos.entry_price,
                    entry_timestamp=dpos.entry_timestamp,
                    current_price=bar.close,
                )

        # 2. Update Peak Equity and Check Emergency Drawdown Halt
        cur_eq = self._broker.total_equity
        if cur_eq > self._peak_equity:
            self._peak_equity = cur_eq

        drawdown = (self._peak_equity - cur_eq) / self._peak_equity
        if drawdown >= self._config.risk.emergency_drawdown_stop_pct:
            self._is_halted = True
            self._journal.record_event(
                event_type=JournalEventType.EMERGENCY_STOP,
                payload={
                    "reason": "Drawdown stop breached",
                    "drawdown_pct": round(drawdown, 6),
                    "peak_equity": self._peak_equity,
                    "current_equity": cur_eq,
                },
                timestamp=step_ts,
            )
            # Cancel all active resting orders
            for o in self._broker.get_all_order_snapshots():
                if not o.is_terminal:
                    self._broker.cancel_order(o.order_id, timestamp=step_ts)

        # 3. Authoritative Reconciliation (INV-009, INV-010)
        acc_snap = self._broker.get_account_snapshot(step_ts)
        local_acc = LocalAccountSnapshot(
            account_id=self._broker.account_id,
            total_equity=acc_snap.total_equity,
            cash_balance=acc_snap.cash_balance,
            currency="USD",
            timestamp=step_ts,
        )
        reconcile_account_state(
            broker_snapshot=acc_snap,
            local_snapshot=local_acc,
            reconciliation_timestamp=step_ts,
        )

        pos_snaps = self._broker.get_position_snapshots(step_ts)
        portfolio_snap = self._build_portfolio_snapshot(step_ts)
        reconcile_positions(
            local_positions=portfolio_snap.positions,
            broker_positions=pos_snaps,
            reconciliation_timestamp=step_ts,
        )

        # 4. Position Maintenance: Invalidation Exits & Pyramiding
        if not self._is_halted:
            for sym in list(self._core_positions.keys()):
                cpos = self._core_positions[sym]
                bar = bars.get(sym)
                if not bar:
                    continue

                # 4a. Check Invalidation Exit (AC-003)
                _, exit_order = evaluate_position_exit(cpos, bar)
                if exit_order is not None:
                    # Invalidation breached: record event and submit SELL market order
                    self._journal.record_event(
                        event_type=JournalEventType.INVALIDATION_TRIGGERED,
                        payload={
                            "symbol": sym,
                            "invalidation_price": round(cpos.invalidation.price, 4),
                            "bar_low": round(bar.low, 4),
                            "bar_close": round(bar.close, 4),
                        },
                        timestamp=step_ts,
                    )
                    exit_intent = OrderIntent(
                        symbol=sym,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=int(cpos.quantity),
                        signal_id=f"EXIT-{exit_order.exit_order_id}",
                        intent_timestamp=step_ts,
                    )
                    submitted_o, _ = self._broker.submit_order(exit_intent)
                    orders_submitted.append(submitted_o.order_id)
                    self._journal.record_event(
                        event_type=JournalEventType.EXECUTION_SUBMITTED,
                        payload={
                            "order_id": submitted_o.order_id,
                            "symbol": sym,
                            "side": OrderSide.SELL.value,
                            "quantity": int(cpos.quantity),
                            "order_type": OrderType.MARKET.value,
                            "reason": "INVALIDATION_STOP",
                        },
                        timestamp=step_ts,
                    )
                    continue

                # 4b. Winner Continuation & Pyramiding (AC-004, AC-005)
                broker_pos = self._broker.get_position(sym)
                if broker_pos and broker_pos.shares > 0:
                    # Confirm winner continuation (anti-take-profit rule)
                    evaluate_winner_continuation(
                        position=cpos,
                        current_price=bar.close,
                        timestamp=step_ts,
                        portfolio_equity=cur_eq,
                        risk_config=self._config.risk,
                    )

                    # Evaluate pyramid eligibility
                    eligibility = evaluate_pyramid_eligibility(
                        position=cpos,
                        current_price=bar.close,
                        strategy_config=self._config.strategy,
                    )
                    if eligibility.is_eligible:
                        ratcheted_p = max(cpos.invalidation.price, cpos.entry_price)
                        self._journal.record_event(
                            event_type=JournalEventType.PYRAMID_ADDED,
                            payload={
                                "symbol": sym,
                                "pyramid_stage": len(cpos.pyramids) + 1,
                                "current_shares": int(cpos.quantity),
                                "current_entry_price": round(cpos.entry_price, 4),
                                "add_price": round(bar.close, 4),
                                "ratcheted_invalidation": round(ratcheted_p, 4),
                            },
                            timestamp=step_ts,
                        )
                        new_inval = InvalidationDefinition(
                            price=ratcheted_p,
                            established_at=step_ts,
                            atr_multiple=cpos.invalidation.atr_multiple,
                            reference_atr=cpos.invalidation.reference_atr,
                            is_ratcheted=True,
                            reason="Ratcheted stop to entry for winner pyramid add",
                        )
                        try:
                            port_snap = self._build_portfolio_snapshot(step_ts)
                            open_risk = sum(p.open_risk_dollars for p in port_snap.positions)
                            pyr_order = create_pyramid_order(
                                position=cpos,
                                add_price=bar.close,
                                new_invalidation=new_inval,
                                portfolio_equity=cur_eq,
                                risk_config=self._config.risk,
                                strategy_config=self._config.strategy,
                                current_open_risk_dollars=open_risk,
                            )
                            add_shares = pyr_order.quantity
                            stop_p = pyr_order.new_invalidation.price
                            risk_d = add_shares * max(0.0, bar.close - stop_p)
                            market_v = add_shares * bar.close

                            self._journal.record_event(
                                event_type=JournalEventType.SIZING_CALCULATED,
                                payload={
                                    "order_type": "PYRAMID",
                                    "symbol": sym,
                                    "allocated_shares": add_shares,
                                    "effective_risk_dollars": round(risk_d, 4),
                                    "market_value": round(market_v, 4),
                                },
                                timestamp=step_ts,
                            )

                            add_intent = OrderIntent(
                                symbol=sym,
                                side=OrderSide.BUY,
                                order_type=OrderType.LIMIT,
                                quantity=add_shares,
                                limit_price=round(bar.close, 4),
                                signal_id=f"PYRAMID-{sym}-{self._step_index}",
                                intent_timestamp=step_ts,
                            )
                            order_id = generate_idempotent_order_id(add_intent)

                            proposed = ProposedExposure(
                                symbol=sym,
                                is_pyramid=True,
                                additional_shares=add_shares,
                                entry_price=round(bar.close, 4),
                                stop_loss_price=round(stop_p, 4),
                                effective_risk_dollars=round(risk_d, 4),
                                effective_market_value=round(market_v, 4),
                            )
                            feed_ctx = feed_context or SystemFeedContext(
                                is_system_operational=True,
                                is_broker_connected=True,
                                is_emergency_stop_active=False,
                                feed_timestamp=step_ts,
                                evaluation_timestamp=step_ts,
                            )

                            # Submit through Guardian Execution Gate (AC-002)
                            receipt, dispatch_res = submit_through_gate(
                                order=add_intent,
                                dispatch_fn=self._broker.submit_order,
                                order_id=order_id,
                                symbol=sym,
                                shares=add_shares,
                                entry_price=round(bar.close, 4),
                                stop_loss_price=round(stop_p, 4),
                                portfolio_state=port_snap,
                                risk_config=self._config.risk,
                                capital_config=self._config.capital,
                                system_feed_context=feed_ctx,
                                proposed_exposure=proposed,
                            )
                            self._journal.record_event(
                                event_type=JournalEventType.RISK_EVALUATED,
                                payload={
                                    "order_id": order_id,
                                    "symbol": sym,
                                    "is_pyramid": True,
                                    "is_approved": receipt.is_approved,
                                    "reasons": list(receipt.rejection_reasons),
                                },
                                timestamp=step_ts,
                            )
                            if receipt.is_approved and dispatch_res is not None:
                                orders_submitted.append(order_id)
                                self._journal.record_event(
                                    event_type=JournalEventType.EXECUTION_SUBMITTED,
                                    payload={
                                        "order_id": order_id,
                                        "symbol": sym,
                                        "side": OrderSide.BUY.value,
                                        "quantity": add_shares,
                                        "order_type": OrderType.LIMIT.value,
                                        "reason": "PYRAMID_ADD",
                                    },
                                    timestamp=step_ts,
                                )
                            elif not receipt.is_approved:
                                if any(
                                    term in r.lower()
                                    for r in receipt.rejection_reasons
                                    for term in (
                                        "disconnected",
                                        "not operational",
                                        "emergency stop",
                                        "stale",
                                    )
                                ):
                                    self._journal.record_event(
                                        event_type=JournalEventType.FAILURE_RECORDED,
                                        payload={
                                            "order_id": order_id,
                                            "symbol": sym,
                                            "rejection_reasons": list(receipt.rejection_reasons),
                                        },
                                        timestamp=step_ts,
                                    )
                        except PyramidingError:
                            pass

            # 4c. Defensive Position Maintenance: De-escalation & Exits
            if self._defensive_universe:
                for sym in list(self._defensive_positions.keys()):
                    dpos = self._defensive_positions[sym]
                    bar = bars.get(sym)
                    if not bar:
                        continue
                    spec = self._get_defensive_spec(sym)
                    if not spec:
                        continue

                    is_norm = normalization_signals is not None and sym in normalization_signals
                    exit_rcpt = evaluate_defensive_position_exit(
                        position=dpos,
                        spec=spec,
                        evaluation_timestamp=step_ts,
                        normalization_signal=is_norm,
                        profit_target_pct=0.30,
                    )
                    if exit_rcpt.action in (
                        DefensiveExitAction.FULL_LIQUIDATION,
                        DefensiveExitAction.PARTIAL_TRIM,
                    ):
                        shares_to_sell = exit_rcpt.shares_to_exit
                        if shares_to_sell > 0:
                            self._journal.record_event(
                                event_type=JournalEventType.DEFENSE_TRIGGERED,
                                payload={
                                    "action": "DEFENSIVE_EXIT",
                                    "symbol": sym,
                                    "reason": exit_rcpt.reason.value,
                                    "shares": shares_to_sell,
                                },
                                timestamp=step_ts,
                            )
                            exit_intent = OrderIntent(
                                symbol=sym,
                                side=OrderSide.SELL,
                                order_type=OrderType.MARKET,
                                quantity=shares_to_sell,
                                signal_id=f"DEF-EXIT-{dpos.position_id}",
                                intent_timestamp=step_ts,
                            )
                            self._pending_defensive_exit_reasons[sym] = exit_rcpt.reason.value
                            submitted_o, _ = self._broker.submit_order(exit_intent)
                            orders_submitted.append(submitted_o.order_id)
                            self._journal.record_event(
                                event_type=JournalEventType.EXECUTION_SUBMITTED,
                                payload={
                                    "order_id": submitted_o.order_id,
                                    "symbol": sym,
                                    "side": OrderSide.SELL.value,
                                    "quantity": shares_to_sell,
                                    "order_type": OrderType.MARKET.value,
                                    "reason": exit_rcpt.reason.value,
                                },
                                timestamp=step_ts,
                            )

        # 5. New Trade Generation (Intake via SignalGenerator & Execution Gate)
        if not self._is_halted:
            allowed_universe = self._config.universe.eligible_symbols or tuple(bars.keys())
            for sym, bar in sorted(bars.items()):
                if sym not in allowed_universe or sym in self._core_positions:
                    continue

                hist = self._bar_history.get(sym, [])
                if len(hist) < self._signal_generator.required_bar_count():
                    continue

                series = BarSeries(
                    symbol=sym,
                    timeframe=self._config.strategy.timeframe,
                    bars=tuple(hist),
                )
                signal = self._signal_generator.generate(series)
                signals_map[sym] = signal.signal_type
                self._journal.record_event(
                    event_type=JournalEventType.SIGNAL_GENERATED,
                    payload={
                        "symbol": sym,
                        "signal_type": signal.signal_type.value,
                        "timeframe": self._config.strategy.timeframe,
                        "bar_close": round(bar.close, 4),
                    },
                    timestamp=step_ts,
                )

                if signal.signal_type == SignalType.BULLISH_ENTRY:
                    # Formulate deterministic entry order
                    entry_order = create_entry_from_signal(
                        signal=signal,
                        strategy_config=self._config.strategy,
                        risk_config=self._config.risk,
                        order_id=f"ENTRY-{sym}-{int(step_ts.timestamp())}",
                    )
                    # Calculate position size
                    port_snap = self._build_portfolio_snapshot(step_ts)
                    open_risk = sum(p.open_risk_dollars for p in port_snap.positions)
                    try:
                        sizing = calculate_position_size(
                            entry_order=entry_order,
                            portfolio_equity=cur_eq,
                            risk_config=self._config.risk,
                            capital_config=self._config.capital,
                            current_open_risk_dollars=open_risk,
                        )
                    except SizingError:
                        continue

                    if sizing.allocated_shares > 0:
                        self._journal.record_event(
                            event_type=JournalEventType.SIZING_CALCULATED,
                            payload={
                                "order_type": "CORE_ENTRY",
                                "symbol": sym,
                                "allocated_shares": sizing.allocated_shares,
                                "effective_risk_dollars": round(sizing.effective_risk_dollars, 4),
                                "market_value": round(sizing.market_value, 4),
                            },
                            timestamp=step_ts,
                        )
                        entry_intent = OrderIntent(
                            symbol=sym,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            quantity=sizing.allocated_shares,
                            signal_id=entry_order.order_id,
                            intent_timestamp=step_ts,
                        )
                        order_id = generate_idempotent_order_id(entry_intent)
                        self._pending_entries[order_id] = entry_order

                        proposed = ProposedExposure(
                            symbol=sym,
                            is_pyramid=False,
                            additional_shares=sizing.allocated_shares,
                            entry_price=round(bar.close, 4),
                            stop_loss_price=round(entry_order.invalidation.price, 4),
                            effective_risk_dollars=round(sizing.effective_risk_dollars, 4),
                            effective_market_value=round(sizing.market_value, 4),
                        )
                        feed_ctx = feed_context or SystemFeedContext(
                            is_system_operational=True,
                            is_broker_connected=True,
                            is_emergency_stop_active=False,
                            feed_timestamp=step_ts,
                            evaluation_timestamp=step_ts,
                        )

                        # Submit through Guardian Execution Gate (AC-002)
                        gate_rcpt, dispatch_res = submit_through_gate(
                            order=entry_intent,
                            dispatch_fn=self._broker.submit_order,
                            order_id=order_id,
                            symbol=sym,
                            shares=sizing.allocated_shares,
                            entry_price=round(bar.close, 4),
                            stop_loss_price=round(entry_order.invalidation.price, 4),
                            portfolio_state=port_snap,
                            risk_config=self._config.risk,
                            capital_config=self._config.capital,
                            system_feed_context=feed_ctx,
                            proposed_exposure=proposed,
                        )
                        self._journal.record_event(
                            event_type=JournalEventType.RISK_EVALUATED,
                            payload={
                                "order_id": order_id,
                                "symbol": sym,
                                "is_pyramid": False,
                                "is_approved": gate_rcpt.is_approved,
                                "reasons": list(gate_rcpt.rejection_reasons),
                            },
                            timestamp=step_ts,
                        )
                        if gate_rcpt.is_approved and dispatch_res is not None:
                            orders_submitted.append(order_id)
                            self._journal.record_event(
                                event_type=JournalEventType.EXECUTION_SUBMITTED,
                                payload={
                                    "order_id": order_id,
                                    "symbol": sym,
                                    "side": OrderSide.BUY.value,
                                    "quantity": sizing.allocated_shares,
                                    "order_type": OrderType.MARKET.value,
                                    "reason": "CORE_ENTRY",
                                },
                                timestamp=step_ts,
                            )
                        elif not gate_rcpt.is_approved:
                            self._pending_entries.pop(order_id, None)
                            if any(
                                term in r.lower()
                                for r in gate_rcpt.rejection_reasons
                                for term in (
                                    "disconnected",
                                    "not operational",
                                    "emergency stop",
                                    "stale",
                                )
                            ):
                                self._journal.record_event(
                                    event_type=JournalEventType.FAILURE_RECORDED,
                                    payload={
                                        "order_id": order_id,
                                        "symbol": sym,
                                        "rejection_reasons": list(gate_rcpt.rejection_reasons),
                                    },
                                    timestamp=step_ts,
                                )

            # 5b. Defensive Crisis Intake (Black Swan Defense)
            if self._defensive_universe and defensive_triggers:
                for trigger in defensive_triggers:
                    if not isinstance(trigger, DefensiveTriggerSignal):
                        raise UnknownDefenseInputError(
                            f"trigger must be DefensiveTriggerSignal, got {type(trigger)}"
                        )
                    if trigger.is_triggered:
                        spec = self._select_defensive_instrument_for_trigger(trigger)
                        if not spec or spec.symbol in self._defensive_positions:
                            continue

                        bar = bars.get(spec.symbol)
                        if not bar:
                            continue

                        self._journal.record_event(
                            event_type=JournalEventType.DEFENSE_TRIGGERED,
                            payload={
                                "action": "CRISIS_INTAKE_TRIGGERED",
                                "symbol": spec.symbol,
                                "trigger_type": trigger.trigger_type.value,
                                "severity_level": trigger.severity_level,
                                "trigger_id": trigger.signal_id,
                            },
                            timestamp=step_ts,
                        )

                        # 1. Enforce Universe Eligibility and Non-Collision (FR-011)
                        enforce_defensive_instrument_eligibility(
                            symbol=spec.symbol,
                            config=self._defensive_universe,
                            evaluation_timestamp=step_ts,
                            current_average_daily_volume=int(bar.volume),
                            current_price=bar.close,
                        )

                        # 2. Black Swan Capital & Sizing
                        port_snap = self._build_portfolio_snapshot(step_ts)
                        cap_snap = calculate_black_swan_capital(
                            portfolio_state=port_snap,
                            config=self._config.capital,
                            timestamp=step_ts,
                        )

                        sizing = enforce_defensive_position_sizing(
                            spec=spec,
                            capital_snapshot=cap_snap,
                            share_price=bar.close,
                            slippage_buffer_pct=self._config.risk.slippage_buffer_pct,
                            trigger_signal=trigger,
                            evaluation_timestamp=step_ts,
                        )

                        if sizing.shares > 0:
                            self._journal.record_event(
                                event_type=JournalEventType.SIZING_CALCULATED,
                                payload={
                                    "order_type": "DEFENSIVE_INTAKE",
                                    "symbol": spec.symbol,
                                    "allocated_shares": sizing.shares,
                                    "effective_risk_dollars": round(
                                        sizing.shares * bar.close * 0.5, 4
                                    ),
                                    "market_value": round(sizing.actual_cost_dollars, 4),
                                },
                                timestamp=step_ts,
                            )
                            # 3. Enforce Capital Intake Safety (INV-013)
                            cap_assess = evaluate_black_swan_capital_intake(
                                portfolio_state=port_snap,
                                proposed_market_value=sizing.actual_cost_dollars,
                                config=self._config.capital,
                                evaluation_timestamp=step_ts,
                                target_sleeve=CapitalSleeve.BLACK_SWAN,
                            )
                            enforce_black_swan_intake_safety(cap_assess)

                            # 4. Formulate Order Intent with Black Swan Sleeve (O2)
                            intent = OrderIntent(
                                symbol=spec.symbol,
                                side=OrderSide.BUY,
                                order_type=OrderType.MARKET,
                                quantity=sizing.shares,
                                signal_id=f"DEF-ENTRY-{spec.symbol}-{trigger.signal_id}-{int(step_ts.timestamp())}",
                                intent_timestamp=step_ts,
                                target_sleeve=CapitalSleeve.BLACK_SWAN,
                            )
                            order_id = generate_idempotent_order_id(intent)
                            self._pending_defensive_entries[order_id] = spec

                            proposed = ProposedExposure(
                                symbol=spec.symbol,
                                is_pyramid=False,
                                additional_shares=sizing.shares,
                                entry_price=round(bar.close, 4),
                                stop_loss_price=round(bar.close * 0.5, 4),
                                effective_risk_dollars=round(sizing.shares * bar.close * 0.5, 4),
                                effective_market_value=round(sizing.actual_cost_dollars, 4),
                                target_sleeve=CapitalSleeve.BLACK_SWAN,
                            )
                            feed_ctx = feed_context or SystemFeedContext(
                                is_system_operational=True,
                                is_broker_connected=True,
                                is_emergency_stop_active=False,
                                feed_timestamp=step_ts,
                                evaluation_timestamp=step_ts,
                            )

                            # 5. Submit through Guardian Execution Gate (AC-004, FR-012, INV-007)
                            gate_rcpt, dispatch_res = submit_through_gate(
                                order=intent,
                                dispatch_fn=self._broker.submit_order,
                                order_id=order_id,
                                symbol=spec.symbol,
                                shares=sizing.shares,
                                entry_price=round(bar.close, 4),
                                stop_loss_price=round(bar.close * 0.5, 4),
                                portfolio_state=port_snap,
                                risk_config=self._config.risk,
                                capital_config=self._config.capital,
                                system_feed_context=feed_ctx,
                                proposed_exposure=proposed,
                            )
                            self._journal.record_event(
                                event_type=JournalEventType.RISK_EVALUATED,
                                payload={
                                    "order_id": order_id,
                                    "symbol": spec.symbol,
                                    "is_approved": gate_rcpt.is_approved,
                                    "reasons": list(gate_rcpt.rejection_reasons),
                                    "sleeve": CapitalSleeve.BLACK_SWAN.value,
                                },
                                timestamp=step_ts,
                            )
                            if gate_rcpt.is_approved and dispatch_res is not None:
                                orders_submitted.append(order_id)
                                self._journal.record_event(
                                    event_type=JournalEventType.EXECUTION_SUBMITTED,
                                    payload={
                                        "order_id": order_id,
                                        "symbol": spec.symbol,
                                        "side": OrderSide.BUY.value,
                                        "quantity": sizing.shares,
                                        "order_type": OrderType.MARKET.value,
                                        "reason": "DEFENSIVE_INTAKE",
                                    },
                                    timestamp=step_ts,
                                )
                            elif not gate_rcpt.is_approved:
                                self._pending_defensive_entries.pop(order_id, None)
                                if any(
                                    term in r.lower()
                                    for r in gate_rcpt.rejection_reasons
                                    for term in (
                                        "disconnected",
                                        "not operational",
                                        "emergency stop",
                                        "stale",
                                    )
                                ):
                                    self._journal.record_event(
                                        event_type=JournalEventType.FAILURE_RECORDED,
                                        payload={
                                            "order_id": order_id,
                                            "symbol": spec.symbol,
                                            "rejection_reasons": list(gate_rcpt.rejection_reasons),
                                        },
                                        timestamp=step_ts,
                                    )

        step_res = PaperSimulationStepResult(
            bar_index=self._step_index,
            timestamp=step_ts,
            signals=signals_map,
            orders_submitted=tuple(orders_submitted),
            fills_executed=tuple(all_fills),
            positions_open=len(self._core_positions) + len(self._defensive_positions),
            cash_balance=round(self._broker.cash_balance, 4),
            total_equity=round(self._broker.total_equity, 4),
            is_halted=self._is_halted,
        )
        self._step_results.append(step_res)
        self._step_index += 1
        return step_res

    def _handle_fill(self, fill: PaperFill, bar: Bar) -> None:
        """Handle execution fills, creating or closing tracked Core & Defensive positions."""
        self._journal.record_event(
            event_type=JournalEventType.BROKER_FILL,
            payload={
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "price": round(fill.price, 4),
                "slippage": round(fill.slippage, 4),
            },
            timestamp=fill.timestamp,
        )

        if fill.side == OrderSide.BUY:
            # Check if this corresponds to a pending entry order
            if fill.order_id in self._pending_entries:
                entry_order = self._pending_entries.pop(fill.order_id)
                self._core_positions[fill.symbol] = CorePosition(
                    position_id=f"POS-{fill.symbol}-{fill.fill_id}",
                    symbol=fill.symbol,
                    entry_order=entry_order,
                    entry_price=fill.price,
                    quantity=float(fill.quantity),
                    invalidation=entry_order.invalidation,
                    status=PositionStatus.OPEN,
                    opened_at=fill.timestamp,
                    average_entry_price=fill.price,
                )
                self._journal.record_event(
                    event_type=JournalEventType.POSITION_CHANGED,
                    payload={
                        "action": "POSITION_OPENED",
                        "position_id": f"POS-{fill.symbol}-{fill.fill_id}",
                        "symbol": fill.symbol,
                        "shares": fill.quantity,
                        "entry_price": round(fill.price, 4),
                        "sleeve": "CORE",
                    },
                    timestamp=fill.timestamp,
                )
            elif fill.order_id in self._pending_defensive_entries:
                self._pending_defensive_entries.pop(fill.order_id)
                pos_id = f"DEF-POS-{fill.symbol}-{fill.fill_id}"
                self._defensive_positions[fill.symbol] = DefensivePosition(
                    position_id=pos_id,
                    symbol=fill.symbol,
                    shares=fill.quantity,
                    entry_price=fill.price,
                    entry_timestamp=fill.timestamp,
                    current_price=fill.price,
                )
                self._journal.record_event(
                    event_type=JournalEventType.POSITION_CHANGED,
                    payload={
                        "action": "DEFENSIVE_POSITION_OPENED",
                        "position_id": pos_id,
                        "symbol": fill.symbol,
                        "shares": fill.quantity,
                        "entry_price": round(fill.price, 4),
                        "sleeve": "BLACK_SWAN",
                    },
                    timestamp=fill.timestamp,
                )
            elif fill.symbol in self._core_positions:
                # Pyramid addition fill
                cpos = self._core_positions[fill.symbol]
                new_qty = cpos.quantity + float(fill.quantity)
                new_avg = (
                    (cpos.quantity * cpos.entry_price) + (fill.quantity * fill.price)
                ) / new_qty
                self._core_positions[fill.symbol] = CorePosition(
                    position_id=cpos.position_id,
                    symbol=cpos.symbol,
                    entry_order=cpos.entry_order,
                    entry_price=round(new_avg, 4),
                    quantity=new_qty,
                    invalidation=cpos.invalidation,
                    status=PositionStatus.OPEN,
                    opened_at=cpos.opened_at,
                    pyramids=cpos.pyramids + (fill.fill_id,),
                    average_entry_price=round(new_avg, 4),
                )
                self._journal.record_event(
                    event_type=JournalEventType.POSITION_CHANGED,
                    payload={
                        "action": "PYRAMID_POSITION_ADDED",
                        "position_id": cpos.position_id,
                        "symbol": fill.symbol,
                        "added_shares": fill.quantity,
                        "total_shares": new_qty,
                        "new_average_entry_price": round(new_avg, 4),
                    },
                    timestamp=fill.timestamp,
                )

        elif fill.side == OrderSide.SELL:
            # Position exit fill
            if fill.symbol in self._core_positions:
                cpos = self._core_positions.pop(fill.symbol)
                pnl = fill.quantity * (fill.price - cpos.entry_price)
                pnl_pct = (fill.price - cpos.entry_price) / cpos.entry_price
                trade_rec = PaperTradeRecord(
                    trade_id=f"TRADE-{cpos.position_id}",
                    symbol=cpos.symbol,
                    shares=fill.quantity,
                    entry_price=cpos.entry_price,
                    exit_price=fill.price,
                    entry_timestamp=cpos.opened_at,
                    exit_timestamp=fill.timestamp,
                    pnl=round(pnl, 4),
                    pnl_pct=round(pnl_pct, 4),
                    exit_reason="STOP_LOSS_INVALIDATION",
                )
                self._completed_trades.append(trade_rec)
                self._journal.record_event(
                    event_type=JournalEventType.POSITION_CHANGED,
                    payload={
                        "action": "POSITION_CLOSED",
                        "position_id": cpos.position_id,
                        "symbol": fill.symbol,
                        "shares": fill.quantity,
                        "exit_price": round(fill.price, 4),
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": "STOP_LOSS_INVALIDATION",
                    },
                    timestamp=fill.timestamp,
                )
            elif fill.symbol in self._defensive_positions:
                dpos = self._defensive_positions.pop(fill.symbol)
                pnl = fill.quantity * (fill.price - dpos.entry_price)
                pnl_pct = (fill.price - dpos.entry_price) / dpos.entry_price
                reason = self._pending_defensive_exit_reasons.pop(
                    fill.symbol, "DEFENSIVE_DEESCALATION"
                )
                trade_rec = PaperTradeRecord(
                    trade_id=f"TRADE-{dpos.position_id}",
                    symbol=dpos.symbol,
                    shares=fill.quantity,
                    entry_price=dpos.entry_price,
                    exit_price=fill.price,
                    entry_timestamp=dpos.entry_timestamp,
                    exit_timestamp=fill.timestamp,
                    pnl=round(pnl, 4),
                    pnl_pct=round(pnl_pct, 4),
                    exit_reason=reason,
                )
                self._completed_trades.append(trade_rec)
                self._journal.record_event(
                    event_type=JournalEventType.POSITION_CHANGED,
                    payload={
                        "action": "DEFENSIVE_POSITION_CLOSED",
                        "position_id": dpos.position_id,
                        "symbol": fill.symbol,
                        "shares": fill.quantity,
                        "exit_price": round(fill.price, 4),
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": reason,
                    },
                    timestamp=fill.timestamp,
                )

    def run_simulation(
        self,
        feed: Sequence[dict[str, Bar]],
        feed_context: SystemFeedContext | None = None,
        defensive_triggers: Sequence[Sequence[DefensiveTriggerSignal] | None] | None = None,
        normalization_signals: Sequence[Sequence[str] | None] | None = None,
    ) -> PaperSimulationSummary:
        """Run complete chronological paper simulation over historical market feed."""
        if not feed:
            raise UnknownPaperInputError("feed sequence cannot be empty.")

        for i, step_bars in enumerate(feed):
            triggers = (
                defensive_triggers[i]
                if defensive_triggers and i < len(defensive_triggers)
                else None
            )
            norms = (
                normalization_signals[i]
                if normalization_signals and i < len(normalization_signals)
                else None
            )
            self.step(
                step_bars,
                feed_context=feed_context,
                defensive_triggers=triggers,
                normalization_signals=norms,
            )

        # Calculate summary statistics
        final_eq = self._broker.total_equity
        ret_pct = (final_eq - self._initial_cash) / self._initial_cash
        total_t = len(self._completed_trades)
        wins = sum(1 for t in self._completed_trades if t.pnl > 0.0)
        losses = sum(1 for t in self._completed_trades if t.pnl <= 0.0)
        win_rate = (wins / total_t) if total_t > 0 else 0.0

        max_dd = 0.0
        peak = self._initial_cash
        for s in self._step_results:
            if s.total_equity > peak:
                peak = s.total_equity
            dd = (peak - s.total_equity) / peak
            if dd > max_dd:
                max_dd = dd

        chk = compute_simulation_checksum(
            initial_equity=self._initial_cash,
            final_equity=final_eq,
            total_trades=total_t,
            completed_trades=self._completed_trades,
        )

        return PaperSimulationSummary(
            initial_equity=round(self._initial_cash, 4),
            final_equity=round(final_eq, 4),
            total_return_pct=round(ret_pct, 4),
            total_trades=total_t,
            winning_trades=wins,
            losing_trades=losses,
            win_rate=round(win_rate, 4),
            max_drawdown_pct=round(max_dd, 4),
            completed_trades=tuple(self._completed_trades),
            receipt_checksum=chk,
        )
