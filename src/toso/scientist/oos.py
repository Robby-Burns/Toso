"""TOSO Scientist Out-of-Sample (OOS) Evaluation Engine.

Evaluates candidate strategy variations against strictly held-out out-of-sample datasets,
computes performance retention ratios and drawdown expansion, detects parameter overfitting,
and enforces execution boundary isolation (FR-017, FR-018, INV-014, INV-008, INV-001, INV-006).
"""

import hashlib
from datetime import datetime
from typing import Any

from toso.config.models import StrategyConfig
from toso.scientist.backtest import (
    calculate_performance_metrics,
    simulate_historical_trading,
)
from toso.scientist.errors import (
    InsufficientOOSDataError,
    InvalidBacktestConfigurationError,
    OOSBoundaryViolationError,
    OverfittingDetectedError,
    ScientistExecutionBreachError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    BacktestResult,
    BacktestTrade,
    DatasetSplit,
    DegradationAnalysis,
    ExperimentDesign,
    OOSEvaluationReceipt,
    OOSEvaluationResult,
    PerformanceMetrics,
    ValidationVerdict,
)
from toso.signals.models import BarSeries


def compute_oos_checksum(
    evaluation_id: str,
    experiment_id: str,
    verdict: ValidationVerdict,
    degradation: DegradationAnalysis,
) -> str:
    """Compute deterministic SHA-256 seal for out-of-sample evaluation receipt."""
    content = (
        f"{evaluation_id}|{experiment_id}|{verdict.value}|"
        f"{degradation.sharpe_retention_ratio:.6f}|"
        f"{degradation.profit_factor_retention_ratio:.6f}|"
        f"{degradation.drawdown_expansion_ratio:.6f}|"
        f"{degradation.net_return_retention_ratio:.6f}"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_oos_dataset_split(split: DatasetSplit) -> None:
    """Validate that split boundaries are chronologically sound for OOS evaluation (FR-018)."""
    if not isinstance(split, DatasetSplit):
        raise UnknownScientistInputError(f"split must be a DatasetSplit, got {type(split)}")
    if split.in_sample_end_date >= split.out_of_sample_end_date:
        raise OOSBoundaryViolationError(
            f"in_sample_end_date ({split.in_sample_end_date.isoformat()}) must precede "
            f"out_of_sample_end_date ({split.out_of_sample_end_date.isoformat()})."
        )


def evaluate_degradation(
    in_sample_metrics: PerformanceMetrics,
    out_of_sample_metrics: PerformanceMetrics,
    min_sharpe_retention: float = 0.50,
    max_drawdown_expansion: float = 2.0,
) -> DegradationAnalysis:
    """Calculate mathematical degradation metrics comparing OOS vs In-Sample performance.

    Args:
        in_sample_metrics: PerformanceMetrics from discovery/in-sample simulation.
        out_of_sample_metrics: PerformanceMetrics from held-out validation simulation.
        min_sharpe_retention: Minimum fraction of in-sample Sharpe retained (default 0.50).
        max_drawdown_expansion: Maximum multiple of in-sample max drawdown (default 2.0).

    Returns:
        DegradationAnalysis with calculated ratios and categorical verdict.

    Raises:
        UnknownScientistInputError: If metric inputs are invalid or missing.
        InvalidBacktestConfigurationError: If retention or expansion thresholds are out of range.
    """
    if not isinstance(in_sample_metrics, PerformanceMetrics):
        raise UnknownScientistInputError(
            f"in_sample_metrics must be PerformanceMetrics, got {type(in_sample_metrics)}"
        )
    if not isinstance(out_of_sample_metrics, PerformanceMetrics):
        raise UnknownScientistInputError(
            f"out_of_sample_metrics must be PerformanceMetrics, got {type(out_of_sample_metrics)}"
        )
    if not (0.0 < min_sharpe_retention <= 1.0):
        raise InvalidBacktestConfigurationError(
            f"min_sharpe_retention must be in (0.0, 1.0], got {min_sharpe_retention}"
        )
    if max_drawdown_expansion <= 1.0:
        raise InvalidBacktestConfigurationError(
            f"max_drawdown_expansion must be > 1.0, got {max_drawdown_expansion}"
        )

    # 1. Sharpe retention ratio
    if in_sample_metrics.sharpe_ratio > 0.0:
        sharpe_retention = out_of_sample_metrics.sharpe_ratio / in_sample_metrics.sharpe_ratio
    else:
        sharpe_retention = 0.0

    # 2. Profit factor retention ratio
    if in_sample_metrics.profit_factor > 0.0:
        pf_retention = out_of_sample_metrics.profit_factor / in_sample_metrics.profit_factor
    else:
        pf_retention = 0.0

    # 3. Drawdown expansion ratio
    if in_sample_metrics.max_drawdown > 0.0:
        dd_expansion = out_of_sample_metrics.max_drawdown / in_sample_metrics.max_drawdown
    else:
        dd_expansion = 1.0

    # 4. Net return retention ratio
    if in_sample_metrics.net_return_pct > 0.0:
        ret_retention = out_of_sample_metrics.net_return_pct / in_sample_metrics.net_return_pct
    else:
        ret_retention = 0.0

    # Categorical classification
    if (
        out_of_sample_metrics.total_trades == 0
        or out_of_sample_metrics.total_pnl <= 0.0
        or out_of_sample_metrics.sharpe_ratio <= 0.0
    ):
        verdict = ValidationVerdict.REJECTED
        notes = "OOS performance yielded non-positive returns, zero trades, or non-positive Sharpe."
    elif sharpe_retention < min_sharpe_retention or dd_expansion > max_drawdown_expansion:
        verdict = ValidationVerdict.OVERFIT
        notes = (
            f"Severe overfitting detected: Sharpe retention {sharpe_retention:.1%} "
            f"< {min_sharpe_retention:.1%} or DD expansion {dd_expansion:.2f}x "
            f"> {max_drawdown_expansion:.2f}x."
        )
    elif sharpe_retention < 0.70 or dd_expansion > 1.50:
        verdict = ValidationVerdict.DEGRADED
        notes = "Moderate performance degradation observed OOS, within allowable threshold."
    else:
        verdict = ValidationVerdict.VALIDATED
        notes = "Performance robustly retained across held-out evaluation window."

    return DegradationAnalysis(
        sharpe_retention_ratio=round(sharpe_retention, 4),
        profit_factor_retention_ratio=round(pf_retention, 4),
        drawdown_expansion_ratio=round(dd_expansion, 4),
        net_return_retention_ratio=round(ret_retention, 4),
        verdict=verdict,
        notes=notes,
    )


def filter_out_of_sample_data(series: BarSeries, split: DatasetSplit) -> BarSeries:
    """Extract bars strictly within held-out out-of-sample window.

    Window: [in_sample_end_date, out_of_sample_end_date) (FR-018).

    Raises:
        OOSBoundaryViolationError: If split boundaries are invalid.
        InsufficientOOSDataError: When no bars fall within the out-of-sample interval.
    """
    validate_oos_dataset_split(split)
    oos_bars = [
        b
        for b in series.bars
        if split.in_sample_end_date <= b.timestamp < split.out_of_sample_end_date
    ]
    if not oos_bars:
        raise InsufficientOOSDataError(
            f"No bars in series for {series.symbol} fall within out-of-sample period "
            f"[{split.in_sample_end_date.isoformat()}, {split.out_of_sample_end_date.isoformat()})."
        )
    return BarSeries(symbol=series.symbol, timeframe=series.timeframe, bars=tuple(oos_bars))


def run_oos_evaluation(
    experiment: ExperimentDesign,
    base_config: StrategyConfig,
    market_data: BarSeries | dict[str, BarSeries],
    initial_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.01,
    min_sharpe_retention: float = 0.50,
    max_drawdown_expansion: float = 2.0,
    fail_on_overfit: bool = False,
    executed_at: datetime | None = None,
) -> tuple[OOSEvaluationResult, OOSEvaluationReceipt]:
    """Execute out-of-sample evaluation of an experiment on held-out validation data.

    Args:
        experiment: ExperimentDesign containing dataset split and parameter variations.
        base_config: Authoritative baseline strategy configuration.
        market_data: Historical bar data spanning in-sample and out-of-sample windows.
        initial_capital: Simulation portfolio equity balance.
        risk_per_trade_pct: Single-trade risk budget.
        min_sharpe_retention: Threshold below which performance is flagged as OVERFIT.
        max_drawdown_expansion: Drawdown multiple above which performance is flagged as OVERFIT.
        fail_on_overfit: If True, raises OverfittingDetectedError when OVERFIT or REJECTED.
        executed_at: Echoed evaluation timestamp anchor.

    Returns:
        Tuple of (OOSEvaluationResult, OOSEvaluationReceipt).

    Raises:
        UnknownScientistInputError: On malformed inputs or missing timestamp.
        InvalidBacktestConfigurationError: On invalid parameter overrides or configuration.
        OOSBoundaryViolationError: On inverted/invalid OOS split boundaries.
        InsufficientOOSDataError: If no out-of-sample bars exist.
        OverfittingDetectedError: If fail_on_overfit is True and strategy is overfit.
    """
    if not isinstance(experiment, ExperimentDesign):
        raise UnknownScientistInputError(
            f"experiment must be an ExperimentDesign, got {type(experiment)}"
        )
    if not isinstance(base_config, StrategyConfig):
        raise InvalidBacktestConfigurationError(
            f"base_config must be a StrategyConfig, got {type(base_config)}"
        )
    if executed_at is None or not isinstance(executed_at, datetime):
        raise UnknownScientistInputError(f"Invalid executed_at timestamp: {executed_at}")

    validate_oos_dataset_split(experiment.dataset_split)

    if isinstance(market_data, BarSeries):
        series_dict = {market_data.symbol: market_data}
    elif isinstance(market_data, dict):
        if not market_data:
            raise UnknownScientistInputError("market_data dictionary cannot be empty.")
        series_dict = market_data
    else:
        raise UnknownScientistInputError(f"Invalid market_data type: {type(market_data)}")

    # 1. Apply Candidate Parameter Variations to StrategyConfig
    override_dict: dict[str, Any] = {}
    valid_fields = set(StrategyConfig.model_fields.keys())

    for variation in experiment.parameter_variations:
        if variation.parameter_name not in valid_fields:
            raise InvalidBacktestConfigurationError(
                f"Unknown parameter '{variation.parameter_name}' for StrategyConfig."
            )
        override_dict[variation.parameter_name] = variation.candidate_value

    try:
        candidate_config = base_config.model_copy(update=override_dict)
    except Exception as e:
        raise InvalidBacktestConfigurationError(
            f"Failed to apply candidate parameter variations: {e}"
        ) from e

    # 2. Run Candidate In-Sample Simulation
    split = experiment.dataset_split
    is_trades: list[BacktestTrade] = []
    is_equity_curves: list[tuple[float, ...]] = []
    capital_per_symbol = initial_capital / len(series_dict)

    for symbol, series in series_dict.items():
        if symbol not in split.symbols:
            continue
        trades, eq = simulate_historical_trading(
            series=series,
            config=candidate_config,
            initial_capital=capital_per_symbol,
            risk_per_trade_pct=risk_per_trade_pct,
            split=split,
            is_in_sample=True,
        )
        is_trades.extend(trades)
        is_equity_curves.append(eq)

    if not is_equity_curves:
        raise UnknownScientistInputError("No matching symbols for in-sample backtest.")

    min_is_len = min(len(c) for c in is_equity_curves)
    agg_is_equity = tuple(round(sum(c[i] for c in is_equity_curves), 4) for i in range(min_is_len))
    is_metrics = calculate_performance_metrics(
        trades=tuple(sorted(is_trades, key=lambda t: t.entry_timestamp)),
        equity_curve=agg_is_equity,
        initial_capital=initial_capital,
    )
    is_result = BacktestResult(
        backtest_id=f"BT-IS-{experiment.experiment_id}",
        experiment_id=experiment.experiment_id,
        config_version=experiment.baseline_config_version,
        parameters=candidate_config.model_dump(),
        metrics=is_metrics,
        trades=tuple(is_trades),
        equity_curve=agg_is_equity,
        start_date=split.start_date,
        end_date=split.in_sample_end_date,
        executed_at=executed_at,
    )

    # 3. Run Candidate Out-of-Sample Simulation (Strict Temporal Partitioning FR-018)
    oos_trades: list[BacktestTrade] = []
    oos_equity_curves: list[tuple[float, ...]] = []

    for symbol, series in series_dict.items():
        if symbol not in split.symbols:
            continue
        # Filter strictly for OOS bars
        oos_series = filter_out_of_sample_data(series, split)
        trades, eq = simulate_historical_trading(
            series=oos_series,
            config=candidate_config,
            initial_capital=capital_per_symbol,
            risk_per_trade_pct=risk_per_trade_pct,
            split=None,  # Already filtered to OOS
            is_in_sample=False,
        )
        oos_trades.extend(trades)
        oos_equity_curves.append(eq)

    min_oos_len = min(len(c) for c in oos_equity_curves)
    agg_oos_equity = tuple(
        round(sum(c[i] for c in oos_equity_curves), 4) for i in range(min_oos_len)
    )
    oos_metrics = calculate_performance_metrics(
        trades=tuple(sorted(oos_trades, key=lambda t: t.entry_timestamp)),
        equity_curve=agg_oos_equity,
        initial_capital=initial_capital,
    )
    oos_result = BacktestResult(
        backtest_id=f"BT-OOS-{experiment.experiment_id}",
        experiment_id=experiment.experiment_id,
        config_version=experiment.baseline_config_version,
        parameters=candidate_config.model_dump(),
        metrics=oos_metrics,
        trades=tuple(oos_trades),
        equity_curve=agg_oos_equity,
        start_date=split.in_sample_end_date,
        end_date=split.out_of_sample_end_date,
        executed_at=executed_at,
    )

    # 4. Degradation and Overfitting Evaluation
    degradation = evaluate_degradation(
        in_sample_metrics=is_metrics,
        out_of_sample_metrics=oos_metrics,
        min_sharpe_retention=min_sharpe_retention,
        max_drawdown_expansion=max_drawdown_expansion,
    )

    verdict = degradation.verdict
    is_approved = verdict == ValidationVerdict.VALIDATED

    if fail_on_overfit and verdict in (ValidationVerdict.OVERFIT, ValidationVerdict.REJECTED):
        raise OverfittingDetectedError(
            f"Strategy failed out-of-sample validation: {verdict.value}. {degradation.notes}"
        )

    eval_id = f"OOS-EVAL-{experiment.experiment_id}"

    evaluation_result = OOSEvaluationResult(
        evaluation_id=eval_id,
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        in_sample_result=is_result,
        out_of_sample_result=oos_result,
        degradation=degradation,
        verdict=verdict,
        is_approved_for_proposal=is_approved,
        evaluated_at=executed_at,
    )

    # 5. Cryptographic Audit Receipt (INV-001, INV-006)
    checksum = compute_oos_checksum(
        evaluation_id=eval_id,
        experiment_id=experiment.experiment_id,
        verdict=verdict,
        degradation=degradation,
    )

    receipt = OOSEvaluationReceipt(
        receipt_id=f"RCPT-OOS-{experiment.experiment_id}",
        evaluation_id=eval_id,
        experiment_id=experiment.experiment_id,
        verdict=verdict,
        timestamp=executed_at,
        checksum=checksum,
        details=degradation.notes,
    )

    return evaluation_result, receipt


class OOSEvaluator:
    """Out-of-Sample Evaluator operating strictly within Scientist boundary (INV-014)."""

    def __init__(self, base_config: StrategyConfig) -> None:
        if not isinstance(base_config, StrategyConfig):
            raise InvalidBacktestConfigurationError("base_config must be a StrategyConfig")
        self.base_config = base_config

    def evaluate(
        self,
        experiment: ExperimentDesign,
        market_data: BarSeries | dict[str, BarSeries],
        initial_capital: float = 100_000.0,
        risk_per_trade_pct: float = 0.01,
        min_sharpe_retention: float = 0.50,
        max_drawdown_expansion: float = 2.0,
        fail_on_overfit: bool = False,
        executed_at: datetime | None = None,
    ) -> tuple[OOSEvaluationResult, OOSEvaluationReceipt]:
        """Execute complete OOS evaluation."""
        return run_oos_evaluation(
            experiment=experiment,
            base_config=self.base_config,
            market_data=market_data,
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            min_sharpe_retention=min_sharpe_retention,
            max_drawdown_expansion=max_drawdown_expansion,
            fail_on_overfit=fail_on_overfit,
            executed_at=executed_at,
        )

    def execute_live_order(self, *args: Any, **kwargs: Any) -> None:
        """Strictly forbidden: Scientist tools cannot execute live trades (INV-014)."""
        raise ScientistExecutionBreachError(
            "OOSEvaluator cannot submit or route live production orders (INV-014 violation)."
        )

    def cancel_live_order(self, *args: Any, **kwargs: Any) -> None:
        """Strictly forbidden: Scientist tools cannot cancel live trades (INV-014)."""
        raise ScientistExecutionBreachError(
            "OOSEvaluator cannot cancel live production orders (INV-014 violation)."
        )
