"""Tests for Story SCI-005: Historical Backtesting Engine.

Verifies:
- FR-017: Reproducible historical backtests, parameter variation evaluation.
- FR-018: Strict in-sample data separation (zero data leakage into validation period).
- INV-014: Research execution boundary isolation (zero live execution authority).
- INV-008: Fail-closed behavior on corrupted, missing, or UNKNOWN inputs.
- INV-001, INV-006: Pure determinism, immutable models, and cryptographic audit receipts.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from toso.config.models import StrategyConfig
from toso.scientist import (
    BacktestComparison,
    BacktestEngine,
    BacktestReceipt,
    BacktestResult,
    BacktestTrade,
    DatasetSplit,
    ExperimentDesign,
    InSampleViolationError,
    InsufficientDataForBacktestError,
    InvalidBacktestConfigurationError,
    ParameterVariation,
    PerformanceMetrics,
    ScientistExecutionBreachError,
    UnknownScientistInputError,
    calculate_performance_metrics,
    compute_backtest_checksum,
    create_experiment_design,
    evaluate_experiment_backtest,
    run_backtest,
    simulate_historical_trading,
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
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


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
def trending_market_data() -> BarSeries:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    # 100 days of upward trending price action
    bars = make_bars(count=100, start_time=start, base_price=100.0, slope=0.8, volatility=1.5)
    return BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))


@pytest.fixture
def valid_dataset_split() -> DatasetSplit:
    return DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2023, 3, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2023, 6, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )


@pytest.fixture
def sample_experiment(
    eval_timestamp: datetime,
    valid_dataset_split: DatasetSplit,
) -> ExperimentDesign:
    return create_experiment_design(
        experiment_id="EXP-LOOKBACK-001",
        hypothesis_id="HYP-BREAKOUT-001",
        title="Breakout Lookback Sensitivity Study",
        description="Evaluate performance of 20-bar lookback vs 15-bar baseline in-sample.",
        baseline_config_version="1.0.0",
        parameter_variations=(
            ParameterVariation(
                parameter_name="breakout_lookback_bars",
                baseline_value=15,
                candidate_value=20,
                rationale="Dampen noise in low-volatility chop regimes.",
            ),
        ),
        dataset_split=valid_dataset_split,
        target_metrics=("sharpe_ratio", "max_drawdown", "profit_factor"),
        created_at=eval_timestamp,
    )


def test_ac001_deterministic_in_sample_historical_simulation(
    trending_market_data: BarSeries,
    test_config: StrategyConfig,
    valid_dataset_split: DatasetSplit,
) -> None:
    """AC-001: Historical simulation generates signals and simulates trades deterministically."""
    trades, equity_curve = simulate_historical_trading(
        series=trending_market_data,
        config=test_config,
        initial_capital=100_000.0,
        risk_per_trade_pct=0.01,
        split=valid_dataset_split,
        is_in_sample=True,
    )

    assert isinstance(trades, tuple)
    assert isinstance(equity_curve, tuple)
    assert len(equity_curve) > 1
    assert equity_curve[0] == 100_000.0

    # Verify trades have valid deterministic fields
    for trade in trades:
        assert trade.symbol == "BTC/USD"
        assert trade.entry_price > 0.0
        assert trade.exit_price > 0.0
        assert trade.shares > 0.0
        assert trade.entry_timestamp < trade.exit_timestamp
        assert trade.exit_reason in (
            "INVALIDATION_GAP",
            "INVALIDATION_STOP",
            "BEARISH_EXIT",
            "PERIOD_END",
        )

    # Identical replay produces bit-for-bit identical results
    replay_trades, replay_equity = simulate_historical_trading(
        series=trending_market_data,
        config=test_config,
        initial_capital=100_000.0,
        risk_per_trade_pct=0.01,
        split=valid_dataset_split,
        is_in_sample=True,
    )
    assert trades == replay_trades
    assert equity_curve == replay_equity


def test_ac002_strict_in_sample_data_boundary_enforcement(
    test_config: StrategyConfig,
) -> None:
    """AC-002: In-sample backtesting rejects data outside [start_date, in_sample_end_date)."""
    # Bars entirely outside the in-sample window
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = make_bars(count=50, start_time=start, base_price=100.0)
    out_of_sample_series = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))

    split = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2023, 6, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2023, 12, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )

    # Must raise InSampleViolationError because no in-sample data exists
    with pytest.raises(InSampleViolationError):
        simulate_historical_trading(
            series=out_of_sample_series,
            config=test_config,
            split=split,
            is_in_sample=True,
        )


def test_ac003_calculate_performance_metrics_accuracy() -> None:
    """AC-003: Accurately computes trade statistics (win rate, profit factor, Sharpe, drawdown)."""
    ts1 = datetime(2025, 1, 1, tzinfo=UTC)
    ts2 = datetime(2025, 1, 5, tzinfo=UTC)
    ts3 = datetime(2025, 1, 10, tzinfo=UTC)
    ts4 = datetime(2025, 1, 15, tzinfo=UTC)

    # Create 2 winning trades and 1 losing trade
    trades = (
        BacktestTrade(
            trade_id="TRD-01",
            symbol="BTC/USD",
            entry_timestamp=ts1,
            entry_price=100.0,
            exit_timestamp=ts2,
            exit_price=110.0,
            shares=10.0,
            pnl=100.0,
            return_pct=0.10,
            exit_reason="BEARISH_EXIT",
        ),
        BacktestTrade(
            trade_id="TRD-02",
            symbol="BTC/USD",
            entry_timestamp=ts2,
            entry_price=110.0,
            exit_timestamp=ts3,
            exit_price=105.0,
            shares=10.0,
            pnl=-50.0,
            return_pct=-0.045455,
            exit_reason="INVALIDATION_STOP",
        ),
        BacktestTrade(
            trade_id="TRD-03",
            symbol="BTC/USD",
            entry_timestamp=ts3,
            entry_price=105.0,
            exit_timestamp=ts4,
            exit_price=120.0,
            shares=10.0,
            pnl=150.0,
            return_pct=0.142857,
            exit_reason="BEARISH_EXIT",
        ),
    )

    equity_curve = (100_000.0, 100_100.0, 100_050.0, 100_200.0)
    metrics = calculate_performance_metrics(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=100_000.0,
    )

    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate == round(2 / 3, 4)
    assert metrics.profit_factor == round(250.0 / 50.0, 4)  # 5.0
    assert metrics.total_pnl == 200.0
    assert metrics.net_return_pct == round(200.0 / 100_000.0, 4)
    # Peak is 100_100, drops to 100_050 -> DD = 50 / 100_100
    expected_dd = 50.0 / 100_100.0
    assert metrics.max_drawdown == round(expected_dd, 4)
    assert metrics.max_drawdown_dollars == 50.0


def test_ac004_baseline_vs_candidate_parameter_variation_evaluation(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    trending_market_data: BarSeries,
    eval_timestamp: datetime,
) -> None:
    """AC-004: Evaluates candidate variation vs baseline under identical in-sample data."""
    comparison, receipt = evaluate_experiment_backtest(
        experiment=sample_experiment,
        base_config=test_config,
        market_data=trending_market_data,
        initial_capital=100_000.0,
        risk_per_trade_pct=0.01,
        executed_at=eval_timestamp,
    )

    assert isinstance(comparison, BacktestComparison)
    assert isinstance(receipt, BacktestReceipt)
    assert comparison.experiment_id == sample_experiment.experiment_id
    assert comparison.hypothesis_id == sample_experiment.hypothesis_id

    # Baseline evaluated with breakout_lookback_bars = 15
    assert comparison.baseline_result.parameters["breakout_lookback_bars"] == 15
    # Candidate evaluated with breakout_lookback_bars = 20
    assert comparison.candidate_result.parameters["breakout_lookback_bars"] == 20

    # Metric deltas exist for all standard metrics
    for metric_name in ("sharpe_ratio", "profit_factor", "win_rate", "max_drawdown", "total_pnl"):
        assert metric_name in comparison.metric_deltas

    # Receipt is valid
    assert receipt.receipt_id.startswith("RCPT-BT-")
    assert receipt.timestamp == eval_timestamp
    assert len(receipt.checksum) == 64


def test_ac005_execution_boundary_isolation(test_config: StrategyConfig) -> None:
    """AC-005: Scientist BacktestEngine cannot execute or cancel live orders (INV-014)."""
    engine = BacktestEngine(base_config=test_config)

    with pytest.raises(
        ScientistExecutionBreachError, match="cannot submit or route live production orders"
    ):
        engine.execute_live_order(symbol="BTC/USD", quantity=1.0)

    with pytest.raises(ScientistExecutionBreachError, match="cannot cancel live production orders"):
        engine.cancel_live_order(order_id="ORD-12345")


def test_ac006_deterministic_audit_receipts(
    sample_experiment: ExperimentDesign,
    test_config: StrategyConfig,
    trending_market_data: BarSeries,
    eval_timestamp: datetime,
) -> None:
    """AC-006: Produces immutable BacktestReceipt with deterministic checksum."""
    comparison, receipt = evaluate_experiment_backtest(
        experiment=sample_experiment,
        base_config=test_config,
        market_data=trending_market_data,
        executed_at=eval_timestamp,
    )

    # Recompute checksum independently and verify match
    expected_checksum = compute_backtest_checksum(
        backtest_id=comparison.candidate_result.backtest_id,
        experiment_id=sample_experiment.experiment_id,
        metrics=comparison.candidate_result.metrics,
        parameters=comparison.candidate_result.parameters,
    )
    assert receipt.checksum == expected_checksum
    assert receipt.timestamp == eval_timestamp


def test_ac007_fail_closed_on_corrupt_unknown_inputs(
    test_config: StrategyConfig,
    trending_market_data: BarSeries,
    eval_timestamp: datetime,
) -> None:
    """AC-007: Corrupted or UNKNOWN inputs fail closed with specific exception types (INV-008)."""
    # 1. Non-positive initial capital
    with pytest.raises(InvalidBacktestConfigurationError):
        calculate_performance_metrics(trades=(), equity_curve=(100.0,), initial_capital=0.0)

    with pytest.raises(InvalidBacktestConfigurationError):
        simulate_historical_trading(
            series=trending_market_data, config=test_config, initial_capital=-1000.0
        )

    # 2. Corrupt / NaN equity curve
    with pytest.raises(UnknownScientistInputError):
        calculate_performance_metrics(
            trades=(), equity_curve=(100.0, float("nan")), initial_capital=100.0
        )

    # 3. Excessive or negative risk per trade
    with pytest.raises(InvalidBacktestConfigurationError):
        simulate_historical_trading(
            series=trending_market_data, config=test_config, risk_per_trade_pct=0.50
        )

    # 4. Insufficient data
    short_bars = make_bars(count=5, start_time=datetime(2025, 1, 1, tzinfo=UTC))
    short_series = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(short_bars))
    with pytest.raises(InsufficientDataForBacktestError):
        simulate_historical_trading(series=short_series, config=test_config)

    # 5. Invalid experiment parameter override (unknown parameter)
    split = DatasetSplit(
        start_date=datetime(2023, 1, 1, tzinfo=UTC),
        in_sample_end_date=datetime(2023, 6, 1, tzinfo=UTC),
        out_of_sample_end_date=datetime(2023, 12, 1, tzinfo=UTC),
        symbols=("BTC/USD",),
    )
    bad_exp = create_experiment_design(
        experiment_id="EXP-BAD-01",
        hypothesis_id="HYP-01",
        title="Invalid param experiment",
        description="Testing unknown param",
        baseline_config_version="1.0.0",
        parameter_variations=(
            ParameterVariation(
                parameter_name="non_existent_trading_param",
                baseline_value=10,
                candidate_value=20,
            ),
        ),
        dataset_split=split,
        target_metrics=("sharpe_ratio",),
        created_at=eval_timestamp,
    )
    with pytest.raises(InvalidBacktestConfigurationError, match="Unknown parameter"):
        evaluate_experiment_backtest(
            experiment=bad_exp,
            base_config=test_config,
            market_data=trending_market_data,
            executed_at=eval_timestamp,
        )


def test_ac008_multi_asset_universe_backtest_support(
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-008: Backtests across multi-asset universes and aggregates portfolio equity curves."""
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars_btc = make_bars(count=80, start_time=start, base_price=100.0, slope=0.8)
    bars_eth = make_bars(count=80, start_time=start, base_price=50.0, slope=0.4)

    market_data = {
        "BTC/USD": BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars_btc)),
        "ETH/USD": BarSeries(symbol="ETH/USD", timeframe="1d", bars=tuple(bars_eth)),
    }

    result = run_backtest(
        backtest_id="BT-MULTI-01",
        experiment_id="EXP-MULTI-01",
        config=test_config,
        market_data=market_data,
        initial_capital=200_000.0,
        executed_at=eval_timestamp,
    )

    assert result.backtest_id == "BT-MULTI-01"
    assert len(result.equity_curve) > 0
    assert result.equity_curve[0] == 200_000.0
    assert isinstance(result.metrics, PerformanceMetrics)


def test_ac009_adverse_gap_and_intraday_exit_handling(test_config: StrategyConfig) -> None:
    """AC-009: Adverse gap down exits at bar open; intraday breach exits at invalidation stop."""
    # First 35 bars trending up to generate an entry
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = make_bars(count=35, start_time=start, base_price=100.0, slope=1.0, volatility=1.0)

    # Next bar: gap down far below the stop
    ts_gap = start + timedelta(days=35)
    gap_bar = Bar(
        timestamp=ts_gap,
        open=90.0,
        high=92.0,
        low=88.0,
        close=91.0,
        volume=1000.0,
    )
    bars.append(gap_bar)

    series = BarSeries(symbol="BTC/USD", timeframe="1d", bars=tuple(bars))
    trades, _ = simulate_historical_trading(series=series, config=test_config)

    assert len(trades) >= 1
    gap_trade = trades[0]
    assert gap_trade.exit_reason == "INVALIDATION_GAP"
    assert gap_trade.exit_price == 90.0  # Filled at gap open


def test_ac010_pure_determinism_and_immutability(
    trending_market_data: BarSeries,
    test_config: StrategyConfig,
    eval_timestamp: datetime,
) -> None:
    """AC-010: Backtest domain models are strictly frozen and immutable (INV-001, INV-006)."""
    result = run_backtest(
        backtest_id="BT-FROZEN-01",
        experiment_id="EXP-FROZEN-01",
        config=test_config,
        market_data=trending_market_data,
        executed_at=eval_timestamp,
    )

    assert isinstance(result, BacktestResult)

    # Verifies immutability
    with pytest.raises(ValidationError):
        result.backtest_id = "MUTATED"

    with pytest.raises(ValidationError):
        result.metrics.win_rate = 0.99
