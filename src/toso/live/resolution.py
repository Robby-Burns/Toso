"""TOSO Live Readiness Question Resolver & Parameter Locking Engine.

Audits, validates, and cryptographically seals the resolution of all blocking
open questions defined in TOSO Specification Section 29.
"""

import hashlib
import json
from datetime import UTC, datetime

from toso.config.models import CoreConfig
from toso.defense.models import DefensiveUniverseConfig
from toso.defense.universe import load_canonical_defensive_universe
from toso.live.errors import InvalidLiveParameterError, UnresolvedLiveQuestionError
from toso.live.models import (
    LiveReadinessAuditReceipt,
    QuestionResolution,
    QuestionStatus,
)


class LiveReadinessResolver:
    """Verifies and seals resolution of all blocking live operational questions."""

    @classmethod
    def resolve_all_questions(
        cls,
        core_config: CoreConfig,
        defense_universe: DefensiveUniverseConfig | None = None,
        audit_time: datetime | None = None,
    ) -> LiveReadinessAuditReceipt:
        """Audit and lock all blocking operational questions against configuration.

        Args:
            core_config: Approved Core strategy and risk configuration.
            defense_universe: Optional defensive universe; loaded canonically if None.
            audit_time: Optional fixed timestamp for deterministic verification.

        Returns:
            LiveReadinessAuditReceipt with SHA-256 digest and status records.

        Raises:
            UnresolvedLiveQuestionError: If any blocking question is unresolved.
            InvalidLiveParameterError: If any parameter violates live safety bounds.
        """
        if defense_universe is None:
            defense_universe = load_canonical_defensive_universe(
                core_symbols=core_config.universe.eligible_symbols
            )

        effective_time = audit_time or datetime.now(UTC)
        resolutions: list[QuestionResolution] = []

        # Q-01: Core Risk Parameters
        if (
            core_config.risk.max_risk_per_trade_pct <= 0.0
            or core_config.risk.max_aggregate_position_risk_pct <= 0.0
            or core_config.risk.max_portfolio_risk_pct <= 0.0
        ):
            raise InvalidLiveParameterError("Core risk percentages must be strictly positive.")

        if (
            core_config.risk.max_risk_per_trade_pct > 0.05
            or core_config.risk.max_portfolio_risk_pct > 0.20
        ):
            raise InvalidLiveParameterError("Core risk parameters exceed safe live ceiling.")

        resolutions.append(
            QuestionResolution(
                question_id="Q-01",
                question_text="What exact Core risk parameters apply?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.risk",
                locked_value={
                    "max_risk_per_trade_pct": core_config.risk.max_risk_per_trade_pct,
                    "max_aggregate_position_risk_pct": (
                        core_config.risk.max_aggregate_position_risk_pct
                    ),
                    "max_portfolio_risk_pct": core_config.risk.max_portfolio_risk_pct,
                    "slippage_buffer_pct": core_config.risk.slippage_buffer_pct,
                    "emergency_drawdown_stop_pct": core_config.risk.emergency_drawdown_stop_pct,
                },
                source="config/core_config_v1.yaml §risk",
            )
        )

        # Q-02: Concentration Thresholds
        if core_config.risk.max_position_concentration_pct > 0.35:
            pct = core_config.risk.max_position_concentration_pct
            raise InvalidLiveParameterError(
                f"Concentration threshold {pct} exceeds 35% safety limit."
            )
        resolutions.append(
            QuestionResolution(
                question_id="Q-02",
                question_text="What exact concentration thresholds/bands should Core use?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.risk.max_position_concentration_pct",
                locked_value=core_config.risk.max_position_concentration_pct,
                source="config/core_config_v1.yaml §risk.max_position_concentration_pct",
            )
        )

        # Q-03: Bar Frequency
        if core_config.strategy.timeframe not in ("1d", "daily"):
            tf = core_config.strategy.timeframe
            raise InvalidLiveParameterError(
                f"Initial Core trading must run on daily timeframe, got '{tf}'."
            )
        resolutions.append(
            QuestionResolution(
                question_id="Q-03",
                question_text="What exact Core signal/bar frequency should be used?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.strategy.timeframe",
                locked_value=core_config.strategy.timeframe,
                source="config/core_config_v1.yaml §strategy.timeframe",
            )
        )

        # Q-04: Deterministic Winner & Strength Rule
        if (
            core_config.strategy.breakout_lookback_bars < 5
            or core_config.strategy.fast_ma_period >= core_config.strategy.slow_ma_period
            or core_config.strategy.invalidation_atr_multiple <= 0.0
        ):
            raise InvalidLiveParameterError("Invalid winner/trend filter parameters.")
        resolutions.append(
            QuestionResolution(
                question_id="Q-04",
                question_text=(
                    "What exact deterministic winner/strength condition should "
                    "the initial Core strategy use?"
                ),
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.strategy",
                locked_value={
                    "breakout_lookback_bars": core_config.strategy.breakout_lookback_bars,
                    "fast_ma_period": core_config.strategy.fast_ma_period,
                    "slow_ma_period": core_config.strategy.slow_ma_period,
                    "atr_period": core_config.strategy.atr_period,
                    "invalidation_atr_multiple": core_config.strategy.invalidation_atr_multiple,
                },
                source="config/core_config_v1.yaml §strategy",
            )
        )

        # Q-05: Pyramiding Risk & Spacing Rules
        if (
            core_config.strategy.pyramid_min_profit_r < 1.0
            or core_config.strategy.max_pyramid_adds < 1
        ):
            raise InvalidLiveParameterError(
                "Pyramiding requires at least 1.0R profit spacing and positive additions."
            )
        resolutions.append(
            QuestionResolution(
                question_id="Q-05",
                question_text=(
                    "What exact pyramiding risk/spacing rules should the initial Core strategy use?"
                ),
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.strategy.pyramiding",
                locked_value={
                    "pyramid_min_profit_r": core_config.strategy.pyramid_min_profit_r,
                    "max_pyramid_adds": core_config.strategy.max_pyramid_adds,
                },
                source="config/core_config_v1.yaml §strategy",
            )
        )

        # Q-06: Approved Black Swan Instruments
        core_set = set(core_config.universe.eligible_symbols)
        defensive_symbols = tuple(spec.symbol for spec in defense_universe.approved_instruments)
        defensive_set = set(defensive_symbols)
        collision = core_set & defensive_set
        if collision:
            raise InvalidLiveParameterError(
                f"Core and Defensive universes must be strictly disjoint. Collision: {collision}"
            )
        if len(defensive_symbols) == 0:
            raise UnresolvedLiveQuestionError("No approved Black Swan defensive instruments.")
        resolutions.append(
            QuestionResolution(
                question_id="Q-06",
                question_text="What exact Black Swan instruments are approved?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="defense.universe.instruments",
                locked_value=defensive_symbols,
                source="toso.defense.universe:load_canonical_defensive_universe",
            )
        )

        # Q-07: Black Swan Allocation & Sizing
        if (
            core_config.capital.black_swan_allocation_pct <= 0.0
            or core_config.capital.black_swan_allocation_pct > 0.15
        ):
            alloc = core_config.capital.black_swan_allocation_pct
            raise InvalidLiveParameterError(
                f"Black Swan allocation {alloc} out of safe bounds (0.01 - 0.15)."
            )
        resolutions.append(
            QuestionResolution(
                question_id="Q-07",
                question_text="What exact Black Swan allocation/sizing parameters apply?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.capital.black_swan_allocation_pct",
                locked_value=core_config.capital.black_swan_allocation_pct,
                source="config/core_config_v1.yaml §capital.black_swan_allocation_pct",
            )
        )

        # Q-08: Short-Sale / Borrow Rules
        resolutions.append(
            QuestionResolution(
                question_id="Q-08",
                question_text="What exact short-sale/borrow rules apply?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.short_selling_enabled",
                locked_value=core_config.short_selling_enabled,
                source="config/core_config_v1.yaml §short_selling_enabled",
            )
        )

        # Q-09: Universe & Eligibility Rules
        if len(core_config.universe.eligible_symbols) == 0:
            raise UnresolvedLiveQuestionError("Core universe eligible symbols cannot be empty.")
        resolutions.append(
            QuestionResolution(
                question_id="Q-09",
                question_text="What exact universe/eligibility rules apply?",
                is_blocking=True,
                status=QuestionStatus.RESOLVED,
                parameter_key="config.universe",
                locked_value={
                    "eligible_symbols": core_config.universe.eligible_symbols,
                    "min_average_daily_volume_shares": (
                        core_config.universe.min_average_daily_volume_shares
                    ),
                    "min_share_price_dollars": core_config.universe.min_share_price_dollars,
                },
                source="config/core_config_v1.yaml §universe",
            )
        )

        # Non-blocking questions (Q-10, Q-11)
        resolutions.append(
            QuestionResolution(
                question_id="Q-10",
                question_text="What tax-lot/wash-sale behavior is required?",
                is_blocking=False,
                status=QuestionStatus.DEFERRED,
                parameter_key="accounting.tax_lots",
                locked_value=(
                    "FIFO / Specific-ID handled at broker level; "
                    "non-blocking for strategy execution."
                ),
                source="Specification Section 29 §Q-10",
            )
        )
        resolutions.append(
            QuestionResolution(
                question_id="Q-11",
                question_text="What market-data redundancy is required?",
                is_blocking=False,
                status=QuestionStatus.DEFERRED,
                parameter_key="feed.redundancy",
                locked_value=(
                    "Fail-closed on primary staleness (INV-008, FR-014); "
                    "secondary feed failover deferred."
                ),
                source="Specification Section 29 §Q-11",
            )
        )

        # Verify 100% of blocking questions are resolved
        blocking_resolutions = [r for r in resolutions if r.is_blocking]
        all_blocking_resolved = all(
            r.status == QuestionStatus.RESOLVED for r in blocking_resolutions
        )
        if not all_blocking_resolved:
            raise UnresolvedLiveQuestionError(
                "Not all blocking operational questions have been resolved."
            )

        # Calculate deterministic cryptographic digest
        hash_payload = {
            "version": core_config.metadata.version,
            "core_symbols": list(core_config.universe.eligible_symbols),
            "defensive_symbols": list(defensive_symbols),
            "resolutions": [
                {
                    "id": r.question_id,
                    "status": r.status.value,
                    "locked_value": r.locked_value,
                }
                for r in resolutions
            ],
        }
        encoded = json.dumps(hash_payload, sort_keys=True).encode("utf-8")
        audit_hash = hashlib.sha256(encoded).hexdigest()

        return LiveReadinessAuditReceipt(
            timestamp=effective_time,
            config_version=core_config.metadata.version,
            all_blocking_resolved=all_blocking_resolved,
            resolutions=tuple(resolutions),
            core_symbols=core_config.universe.eligible_symbols,
            defensive_symbols=defensive_symbols,
            audit_hash=audit_hash,
        )
