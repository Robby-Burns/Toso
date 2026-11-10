"""Tests for Story SCI-006: Out-of-Sample (OOS) Evaluation.

Verifies:
- FR-017: Controlled research process, reproducible held-out evaluation.
- FR-018: Strict temporal separation between discovery and validation, overfitting detection.
- INV-014: Scientist execution boundary isolation (zero live execution authority).
- INV-008: Fail-closed handling on corrupt, missing, non-chronological, or UNKNOWN inputs.
- INV-001, INV-006: Pure determinism, immutable models, and cryptographic audit receipts.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.config.models import StrategyConfig
from toso.scientist import (
    DatasetSplit,
    DegradationAnalysis,
    ExperimentDesign,
    InsufficientOOSDataError,
    InvalidBacktestConfigurationError,
    OOSBoundaryViolationError,
    OOSEvaluationReceipt,
    OOSEvaluationResult,
    OOSEvaluator,
    OverfittingDetectedError,
    ParameterVariation,
    PerformanceMetrics,
    ScientistExecutionBreachError,
    UnknownScientistInputError,
    ValidationVerdict,
    compute_oos_checksum,
    create_experiment_design,
    evaluate_degradation,
    filter_out_of_sample_data,
    run_oos_evaluation,
)
from toso.signals.models import Bar, BarSeries


def make_bars(
    count: int,
    start_time: datetime,
    base_price: float = 100.0,
    slope: float = 0.5,
    volatility: float = 2.0,
) -> list[Bar]:
    """Generate chronologically consistent synthetic OHLCV bars."""
    bars: list[Bar] = []
    current_price = base_price

    for i in range(count):
        ts = start_time + timedelta(days=i)
        current_price += slope
        half_vol = volatility / 2.0

        bar_open = current_price - 0.2
        bar_high = current_price + half_vol
        bar_low = current_price - half_vol
        bar_close = current_price + 0.1

        bar_high = max(bar_high, bar_open, bar_close)
        bar_low = min(bar_low, bar_open, bar_close)

        bars.append(
            Bar(
                timestamp=ts,
                open=round(bar_open, 4),
                high=round(bar_high, 4),
                low=round(bar_low, 4),
                close=round(bar_close, 4),
                volume=1000.0,
            )
        )
    return bars


@pytest.fixture
def eval_timestamp() -> datetime:
    return datetime(2026, 9, 15, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def test_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="test_trend_v1",
        timeframe="1d",
        fast_ma_period=10,
        slow_ma_period=30,
        atr_period=10,
        breakout_lookback_bars=15,
        invalidation_atr_multiple=2.0,
    )


@pytest.fixture
def two_year_split() -> DatasetSplit:
    return DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2023, 7, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2024, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )


@pytest.fixture
def sample_experiment(
    eval_timestamp: datetime,
    two_year_split: DatasetSplit,
) -> ExperimentDesign:
    return create_experiment_design(
        experiment_id="EXP-LOOKBACK-002",
        hypothesis_id="HYP-BREAKOUT-002",
        title="Breakout Lookback OOS Validation",
        description="Verify lookback 20 robustness on held-out validation window.",
        baseline_config_version="1.0.0",
        parameter_variations=(
            ParameterVariation(
                parameter_name="breakout_lookback_bars",
                baseline_value=15,
                candidate_value=20,
                rationale="Evaluate generalization out-of-sample.",
            ),
        ),
        dataset_split=two_year_split,
        target_metrics=("sharpe_ratio", "max_drawdown"),
        created_at=eval_timestamp,
    )


def test_ac001_oos_evaluation_execution_and_metrics(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-001: Executes OOS evaluation, computing degradation analysis and producing receipt."""
    # 365 bars covering entire IS and OOS range
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = make_bars(count=365, start_time=start, base_price=100.0, slope=0.5, volatility=1.5)
    market_data = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))

    result, receipt = run_oos_evaluation(
        experiment=sample_experiment,
        base_config=test_config,
        market_data=market_data,
        executed_at=eval_timestamp,
    )

    assert isinstance(result, OOSEvaluationResult)
    assert isinstance(receipt, OOSEvaluationReceipt)
    assert result.experiment_id == sample_experiment.experiment_id
    assert result.hypothesis_id == sample_experiment.hypothesis_id
    assert isinstance(result.degradation, DegradationAnalysis)
    assert result.verdict in list(ValidationVerdict)
    assert receipt.checksum == compute_oos_checksum(
        result.evaluation_id, result.experiment_id, result.verdict, result.degradation
    )


def test_ac002_strict_oos_data_isolation_and_leakage_prevention(
    two_year_split: DatasetSplit,
) -> None:
    """AC-002: OOS data extraction enforces [is_end, oos_end) split boundaries (FR-018)."""
    # Bars entirely inside in-sample window
    start = datetime(2023, 1, 1, tzinfo=UTC)
    is_only_bars = make_bars(count=60, start_time=start, base_price=100.0)
    is_series = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(is_only_bars))

    with pytest.raises(InsufficientOOSDataError):
        filter_out_of_sample_data(is_series, two_year_split)

    # Inverted dates fail closed
    bad_split = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2024, 1, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2023, 6, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )
    with pytest.raises(OOSBoundaryViolationError):
        filter_out_of_sample_data(is_series, bad_split)


def test_ac003_overfitting_detection_and_rejection() -> None:
    """AC-003: Flags severe performance degradation as OVERFIT or REJECTED."""
    is_metrics = PerformanceMetrics(
        total_trades=20,
        winning_trades=14,
        losing_trades=6,
        win_rate=0.70,
        profit_factor=2.50,
        sharpe_ratio=2.00,
        max_drawdown=0.08,
        max_drawdown_dollars=8000.0,
        total_pnl=30000.0,
        net_return_pct=0.30,
    )

    # Case A: Severe Sharpe drop (< 50% of IS Sharpe)
    overfit_oos_metrics = PerformanceMetrics(
        total_trades=15,
        winning_trades=7,
        losing_trades=8,
        win_rate=0.4667,
        profit_factor=1.10,
        sharpe_ratio=0.60,  # 0.60 / 2.00 = 30% retention < 50%
        max_drawdown=0.15,
        max_drawdown_dollars=15000.0,
        total_pnl=4000.0,
        net_return_pct=0.04,
    )
    analysis = evaluate_degradation(is_metrics, overfit_oos_metrics, min_sharpe_retention=0.50)
    assert analysis.verdict == ValidationVerdict.OVERFIT
    assert analysis.sharpe_retention_ratio == 0.30

    # Case B: Severe drawdown expansion (> 2.0x IS DD)
    dd_expansion_metrics = PerformanceMetrics(
        total_trades=15,
        winning_trades=10,
        losing_trades=5,
        win_rate=0.6667,
        profit_factor=1.80,
        sharpe_ratio=1.50,  # Good Sharpe
        max_drawdown=0.20,  # 0.20 / 0.08 = 2.5x expansion > 2.0x
        max_drawdown_dollars=20000.0,
        total_pnl=15000.0,
        net_return_pct=0.15,
    )
    analysis_dd = evaluate_degradation(is_metrics, dd_expansion_metrics, max_drawdown_expansion=2.0)
    assert analysis_dd.verdict == ValidationVerdict.OVERFIT
    assert analysis_dd.drawdown_expansion_ratio == 2.50

    # Case C: Negative return or 0 trades -> REJECTED
    losing_oos_metrics = PerformanceMetrics(
        total_trades=10,
        winning_trades=2,
        losing_trades=8,
        win_rate=0.20,
        profit_factor=0.50,
        sharpe_ratio=-0.80,
        max_drawdown=0.25,
        max_drawdown_dollars=25000.0,
        total_pnl=-10000.0,
        net_return_pct=-0.10,
    )
    analysis_loss = evaluate_degradation(is_metrics, losing_oos_metrics)
    assert analysis_loss.verdict == ValidationVerdict.REJECTED


def test_ac004_robust_strategy_validation() -> None:
    """AC-004: Validates strategy when performance is robustly retained across held-out OOS data."""
    is_metrics = PerformanceMetrics(
        total_trades=25,
        winning_trades=16,
        losing_trades=9,
        win_rate=0.64,
        profit_factor=2.20,
        sharpe_ratio=1.80,
        max_drawdown=0.10,
        max_drawdown_dollars=10000.0,
        total_pnl=25000.0,
        net_return_pct=0.25,
    )

    robust_oos_metrics = PerformanceMetrics(
        total_trades=20,
        winning_trades=13,
        losing_trades=7,
        win_rate=0.65,
        profit_factor=2.00,
        sharpe_ratio=1.60,  # 1.60 / 1.80 = 88.9% retention
        max_drawdown=0.11,  # 1.1x expansion
        max_drawdown_dollars=11000.0,
        total_pnl=20000.0,
        net_return_pct=0.20,
    )

    analysis = evaluate_degradation(is_metrics, robust_oos_metrics)
    assert analysis.verdict == ValidationVerdict.VALIDATED
    assert analysis.sharpe_retention_ratio >= 0.80


def test_ac005_execution_boundary_isolation(test_config: StrategyConfig) -> None:
    """AC-005: Scientist OOSEvaluator cannot execute or route live orders (INV-014)."""
    evaluator = OOSEvaluator(base_config=test_config)

    with pytest.raises(
        ScientistExecutionBreachError, match="cannot submit or route live production orders"
    ):
        evaluator.execute_live_order(symbol="BTC/USD", quantity=1.0)

    with pytest.raises(ScientistExecutionBreachError, match="cannot cancel live production orders"):
        evaluator.cancel_live_order(order_id="ORD-LIVE-01")


def test_ac006_deterministic_audit_receipts(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-006: Produces deterministic OOSEvaluationReceipt with echoed timestamp."""
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = make_bars(count=365, start_time=start, base_price=100.0, slope=0.5)
    market_data = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))

    result, receipt = run_oos_evaluation(
        experiment=sample_experiment,
        base_config=test_config,
        market_data=market_data,
        executed_at=eval_timestamp,
    )

    assert receipt.timestamp == eval_timestamp
    assert receipt.receipt_id.startswith("RCPT-OOS-")
    assert len(receipt.checksum) == 64


def test_ac007_fail_closed_on_corrupt_unknown_inputs(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-007: Corrupted or UNKNOWN inputs fail closed with specific exception types (INV-008)."""
    # 1. Non-datetime timestamp
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = make_bars(count=365, start_time=start, base_price=100.0)
    market_data = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))

    with pytest.raises(UnknownScientistInputError):
        run_oos_evaluation(
            experiment=sample_experiment,
            base_config=test_config,
            market_data=market_data,
            executed_at=None,
        )

    # 2. Invalid threshold parameters
    valid_m = PerformanceMetrics(
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=1.0,
        profit_factor=999.0,
        sharpe_ratio=1.0,
        max_drawdown=0.01,
        max_drawdown_dollars=10.0,
        total_pnl=100.0,
        net_return_pct=0.01,
    )
    with pytest.raises(InvalidBacktestConfigurationError):
        evaluate_degradation(valid_m, valid_m, min_sharpe_retention=0.0)

    with pytest.raises(InvalidBacktestConfigurationError):
        evaluate_degradation(valid_m, valid_m, max_drawdown_expansion=0.8)

    # 3. fail_on_overfit raises OverfittingDetectedError
    with pytest.raises(OverfittingDetectedError):
        # Force fail_on_overfit with very high retention threshold
        run_oos_evaluation(
            experiment=sample_experiment,
            base_config=test_config,
            market_data=market_data,
            min_sharpe_retention=0.99,  # Unlikely to hit 99% retention
            fail_on_overfit=True,
            executed_at=eval_timestamp,
        )


def test_ac008_multi_asset_universe_oos_evaluation(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-008: Evaluates multi-asset portfolios across held-out out-of-sample window."""
    split = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2023, 7, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2024, 1, 1, tzinfo=UTC),
        symbols=("BTC/USD", "ETH/USD"),
    )
    multi_exp = create_experiment_design(
        experiment_id="EXP-MULTI-OOS",
        hypothesis_id="HYP-MULTI-01",
        title="Multi-Asset OOS Test",
        description="Testing universe portfolio OOS",
        baseline_config_version="1.0.0",
        parameter_variations=(
            ParameterVariation(
                parameter_name="breakout_lookback_bars",
                baseline_value=15,
                candidate_value=20,
            ),
        ),
        dataset_split=split,
        target_metrics=("sharpe_ratio",),
        created_at=eval_timestamp,
    )

    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars_btc = make_bars(count=365, start_time=start, base_price=100.0, slope=0.5)
    bars_eth = make_bars(count=365, start_time=start, base_price=50.0, slope=0.3)
    market_data = {
        "BTC/USD": BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars_btc)),
        "ETH/USD": BarSeries(symbol="ETH/USD", timeframe="1d", bars=tuple(bars_eth)),
    }

    result, receipt = run_oos_evaluation(
        experiment=multi_exp,
        base_config=test_config,
        market_data=market_data,
        executed_at=eval_timestamp,
    )

    assert result.experiment_id == "EXP-MULTI-OOS"
    assert isinstance(result.degradation, DegradationAnalysis)
    assert receipt.receipt_id == "RCPT-OOS-EXP-MULTI-OOS"


def test_ac009_pure_determinism_and_immutability(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-009: OOS domain models are strictly frozen and immutable (INV-001, INV-006)."""
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = make_bars(count=365, start_time=start, base_price=100.0, slope=0.5)
    market_data = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))

    result, _ = run_oos_evaluation(
        experiment=sample_experiment,
        base_config=test_config,
        market_data=market_data,
        executed_at=eval_timestamp,
    )

    with pytest.raises(ValidationError):
        result.evaluation_id = "MUTATED"

    with pytest.raises(ValidationError):
        result.degradation.verdict = ValidationVerdict.VALIDATED
