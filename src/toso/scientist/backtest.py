"""TOSO Scientist Historical Backtesting Engine.

Provides deterministic, reproducible in-sample simulation of Core trend-following strategies,
calculating trade metrics (Sharpe ratio, max drawdown, win rate, profit factor),
evaluating parameter variations against baseline configurations, and strictly
enforcing execution boundary isolation (FR-017, FR-018, INV-014, INV-008, INV-001, INV-006).
"""

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from toso.config.models import StrategyConfig
from toso.scientist.errors import (
    InSampleViolationError,
    InsufficientDataForBacktestError,
    InvalidBacktestConfigurationError,
    ScientistExecutionBreachError,
    UnknownScientistInputError,
)
from toso.scientist.models import (
    BacktestComparison,
    BacktestReceipt,
    BacktestResult,
    BacktestTrade,
    DatasetSplit,
    ExperimentDesign,
    PerformanceMetrics,
)
from toso.signals.generator import DeterministicSignalGenerator
from toso.signals.models import BarSeries, SignalType


def compute_backtest_checksum(
    backtest_id: str,
    experiment_id: str,
    metrics: PerformanceMetrics,
    parameters: dict[str, Any],
) -> str:
    """Compute deterministic SHA-256 hash sealing backtest execution results."""
    try:
        param_str = json.dumps(parameters, sort_keys=True, default=str)
    except Exception as e:
        raise UnknownScientistInputError(f"Failed to serialize parameters: {e}") from e

    metrics_str = (
        f"{metrics.total_trades}|{metrics.winning_trades}|{metrics.losing_trades}|"
        f"{metrics.win_rate:.6f}|{metrics.profit_factor:.6f}|{metrics.sharpe_ratio:.6f}|"
        f"{metrics.max_drawdown:.6f}|{metrics.total_pnl:.4f}|{metrics.net_return_pct:.6f}"
    )
    content = f"{backtest_id}|{experiment_id}|{param_str}|{metrics_str}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def calculate_performance_metrics(
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[float],
    initial_capital: float,
) -> PerformanceMetrics:
    """Calculate comprehensive, deterministic performance metrics from trades and equity curve.

    Args:
        trades: Sequence of completed BacktestTrade records.
        equity_curve: Monotonically ordered sequence of simulated portfolio equity values.
        initial_capital: Starting equity balance (must be strictly positive).

    Returns:
        Immutable PerformanceMetrics instance.

    Raises:
        InvalidBacktestConfigurationError: If initial_capital <= 0.
        UnknownScientistInputError: If equity_curve is empty or contains non-numeric/NaN values.
    """
    if (
        not isinstance(initial_capital, (int, float))
        or initial_capital <= 0.0
        or math.isnan(initial_capital)
    ):
        raise InvalidBacktestConfigurationError(
            f"initial_capital must be a positive number, got {initial_capital}."
        )
    if not equity_curve:
        raise UnknownScientistInputError("equity_curve must contain at least one point.")
    for val in equity_curve:
        if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
            raise UnknownScientistInputError(f"Corrupt or non-finite equity value: {val}")

    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl < 0)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = 999.0  # Capped deterministic representation for zero-loss runs
    else:
        profit_factor = 0.0

    # Drawdown calculations from equity curve
    peak = equity_curve[0]
    max_drawdown = 0.0
    max_drawdown_dollars = 0.0

    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd_dollars = peak - eq
        dd_pct = dd_dollars / peak if peak > 0 else 0.0
        if dd_pct > max_drawdown:
            max_drawdown = dd_pct
        if dd_dollars > max_drawdown_dollars:
            max_drawdown_dollars = dd_dollars

    # Annualized Sharpe ratio calculation from periodic equity returns
    if len(equity_curve) >= 2:
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if len(returns) >= 2:
            mean_ret = sum(returns) / len(returns)
            var_ret = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            std_ret = math.sqrt(var_ret)
            if std_ret > 1e-9:
                sharpe_ratio = (mean_ret / std_ret) * math.sqrt(252)
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0
    else:
        sharpe_ratio = 0.0

    total_pnl = equity_curve[-1] - initial_capital
    net_return_pct = total_pnl / initial_capital

    return PerformanceMetrics(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        sharpe_ratio=round(sharpe_ratio, 4),
        max_drawdown=round(max_drawdown, 4),
        max_drawdown_dollars=round(max_drawdown_dollars, 4),
        total_pnl=round(total_pnl, 4),
        net_return_pct=round(net_return_pct, 4),
    )


def simulate_historical_trading(
    series: BarSeries,
    config: StrategyConfig,
    initial_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.01,
    split: DatasetSplit | None = None,
    is_in_sample: bool = True,
) -> tuple[tuple[BacktestTrade, ...], tuple[float, ...]]:
    """Simulate deterministic historical trading for a single instrument.

    Args:
        series: Chronologically sorted BarSeries.
        config: StrategyConfig specifying technical indicator parameters.
        initial_capital: Starting capital for simulation.
        risk_per_trade_pct: Fraction of portfolio equity risked per entry.
        split: Optional DatasetSplit for strict in-sample data filtering.
        is_in_sample: If True and split is provided, strictly limits evaluation to in-sample period.

    Returns:
        Tuple of (completed_trades, equity_curve).

    Raises:
        UnknownScientistInputError: On malformed series or invalid types.
        InvalidBacktestConfigurationError: On invalid capital or risk inputs.
        InSampleViolationError: When in-sample split bounds are violated or empty.
        InsufficientDataForBacktestError: When bars are fewer than required for indicators.
    """
    if not isinstance(series, BarSeries):
        raise UnknownScientistInputError(f"series must be a BarSeries instance, got {type(series)}")
    if not isinstance(config, StrategyConfig):
        raise InvalidBacktestConfigurationError(
            f"config must be a StrategyConfig instance, got {type(config)}"
        )
    if not isinstance(initial_capital, (int, float)) or initial_capital <= 0.0:
        raise InvalidBacktestConfigurationError(
            f"initial_capital must be positive, got {initial_capital}"
        )
    if (
        not isinstance(risk_per_trade_pct, (int, float))
        or risk_per_trade_pct <= 0.0
        or risk_per_trade_pct > 0.10
    ):
        raise InvalidBacktestConfigurationError(
            f"risk_per_trade_pct must be in (0.0, 0.10], got {risk_per_trade_pct}"
        )

    # In-Sample Temporal Partitioning (FR-018)
    if split is not None and is_in_sample:
        in_sample_bars = [
            b for b in series.bars if split.start_date <= b.timestamp < split.in_sample_end_date
        ]
        if not in_sample_bars:
            raise InSampleViolationError(
                f"No bars in series for {series.symbol} fall within in-sample period "
                f"[{split.start_date.isoformat()}, {split.in_sample_end_date.isoformat()})."
            )
        eval_series = BarSeries(
            symbol=series.symbol,
            timeframe=series.timeframe,
            bars=tuple(in_sample_bars),
        )
    else:
        eval_series = series

    generator = DeterministicSignalGenerator(config)
    req_bars = generator.required_bar_count()

    if len(eval_series) < req_bars:
        raise InsufficientDataForBacktestError(
            f"Bar series for {eval_series.symbol} has {len(eval_series)} bars, "
            f"but {req_bars} are required for deterministic indicator evaluation."
        )

    equity = float(initial_capital)
    equity_curve: list[float] = [equity]
    trades: list[BacktestTrade] = []
    open_position: dict[str, Any] | None = None

    for bar_idx in range(req_bars - 1, len(eval_series)):
        current_bar = eval_series.bars[bar_idx]

        # 1. Evaluate Exit on Open Position
        if open_position is not None:
            exit_price: float | None = None
            exit_reason: str | None = None

            # Branch 1a: Adverse Gap Down through invalidation stop
            if current_bar.open <= open_position["invalidation_price"]:
                exit_price = current_bar.open
                exit_reason = "INVALIDATION_GAP"
            # Branch 1b: Intraday Invalidation Stop breach
            elif current_bar.low <= open_position["invalidation_price"]:
                exit_price = open_position["invalidation_price"]
                exit_reason = "INVALIDATION_STOP"
            else:
                # Branch 1c: Bearish Exit Signal
                sub_series = BarSeries(
                    symbol=eval_series.symbol,
                    timeframe=eval_series.timeframe,
                    bars=eval_series.bars[: bar_idx + 1],
                )
                sig = generator.generate(sub_series)
                if sig.signal_type == SignalType.BEARISH_EXIT:
                    exit_price = current_bar.close
                    exit_reason = "BEARISH_EXIT"

            if exit_price is not None and exit_reason is not None:
                pnl = (exit_price - open_position["entry_price"]) * open_position["shares"]
                ret_pct = (exit_price - open_position["entry_price"]) / open_position["entry_price"]
                equity += pnl

                trade = BacktestTrade(
                    trade_id=f"TRD-{eval_series.symbol}-{len(trades) + 1:04d}",
                    symbol=eval_series.symbol,
                    entry_timestamp=open_position["entry_timestamp"],
                    entry_price=round(open_position["entry_price"], 4),
                    exit_timestamp=current_bar.timestamp,
                    exit_price=round(exit_price, 4),
                    shares=round(open_position["shares"], 4),
                    pnl=round(pnl, 4),
                    return_pct=round(ret_pct, 6),
                    exit_reason=exit_reason,
                )
                trades.append(trade)
                open_position = None

        # 2. Evaluate Entry Signal (when flat)
        if open_position is None:
            sub_series = BarSeries(
                symbol=eval_series.symbol,
                timeframe=eval_series.timeframe,
                bars=eval_series.bars[: bar_idx + 1],
            )
            sig = generator.generate(sub_series)

            if sig.signal_type == SignalType.BULLISH_ENTRY and sig.invalidation_price is not None:
                risk_per_share = sig.price - sig.invalidation_price
                if risk_per_share > 0:
                    risk_budget = equity * risk_per_trade_pct
                    shares = risk_budget / risk_per_share
                    # Intake concentration cap: maximum 25% of equity in single position
                    max_notional = equity * 0.25
                    shares = min(shares, max_notional / sig.price)
                    if shares > 0:
                        open_position = {
                            "entry_timestamp": current_bar.timestamp,
                            "entry_price": sig.price,
                            "invalidation_price": sig.invalidation_price,
                            "shares": shares,
                        }

        # 3. Mark-to-Market Equity Record
        if open_position is not None:
            unrealized_pnl = (current_bar.close - open_position["entry_price"]) * open_position[
                "shares"
            ]
            current_equity = equity + unrealized_pnl
        else:
            current_equity = equity

        equity_curve.append(round(current_equity, 4))

    # Close any open position at period end
    if open_position is not None:
        last_bar = eval_series.bars[-1]
        exit_price = last_bar.close
        pnl = (exit_price - open_position["entry_price"]) * open_position["shares"]
        ret_pct = (exit_price - open_position["entry_price"]) / open_position["entry_price"]
        equity += pnl

        trade = BacktestTrade(
            trade_id=f"TRD-{eval_series.symbol}-{len(trades) + 1:04d}",
            symbol=eval_series.symbol,
            entry_timestamp=open_position["entry_timestamp"],
            entry_price=round(open_position["entry_price"], 4),
            exit_timestamp=last_bar.timestamp,
            exit_price=round(exit_price, 4),
            shares=round(open_position["shares"], 4),
            pnl=round(pnl, 4),
            return_pct=round(ret_pct, 6),
            exit_reason="PERIOD_END",
        )
        trades.append(trade)
        equity_curve[-1] = round(equity, 4)

    return tuple(trades), tuple(equity_curve)


def run_backtest(
    backtest_id: str,
    experiment_id: str,
    config: StrategyConfig,
    market_data: BarSeries | dict[str, BarSeries],
    initial_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.01,
    split: DatasetSplit | None = None,
    is_in_sample: bool = True,
    executed_at: datetime | None = None,
) -> BacktestResult:
    """Run historical backtest over one or more instruments and compute performance metrics.

    Args:
        backtest_id: Deterministic backtest identifier.
        experiment_id: Associated experiment identifier.
        config: Strategy configuration under evaluation.
        market_data: Either a single BarSeries or a dictionary mapping ticker to BarSeries.
        initial_capital: Starting total portfolio equity.
        risk_per_trade_pct: Risk budget per trade.
        split: Optional DatasetSplit for in-sample partitioning.
        is_in_sample: If True, restricts simulation to in-sample period.
        executed_at: Echoed evaluation timestamp anchor.

    Returns:
        Immutable BacktestResult.
    """
    if not backtest_id or not isinstance(backtest_id, str):
        raise UnknownScientistInputError(f"Invalid backtest_id: {backtest_id}")
    if not experiment_id or not isinstance(experiment_id, str):
        raise UnknownScientistInputError(f"Invalid experiment_id: {experiment_id}")
    if executed_at is None or not isinstance(executed_at, datetime):
        raise UnknownScientistInputError(f"Invalid executed_at timestamp: {executed_at}")

    if isinstance(market_data, BarSeries):
        series_dict = {market_data.symbol: market_data}
    elif isinstance(market_data, dict):
        if not market_data:
            raise UnknownScientistInputError("market_data dictionary cannot be empty.")
        series_dict = market_data
    else:
        raise UnknownScientistInputError(f"Invalid market_data type: {type(market_data)}")

    all_trades: list[BacktestTrade] = []
    # Capital allocated across instruments
    capital_per_symbol = initial_capital / len(series_dict)
    combined_equity_curves: list[tuple[float, ...]] = []

    start_dates: list[datetime] = []
    end_dates: list[datetime] = []

    for symbol, series in series_dict.items():
        if split is not None and symbol not in split.symbols:
            continue
        trades, eq_curve = simulate_historical_trading(
            series=series,
            config=config,
            initial_capital=capital_per_symbol,
            risk_per_trade_pct=risk_per_trade_pct,
            split=split,
            is_in_sample=is_in_sample,
        )
        all_trades.extend(trades)
        combined_equity_curves.append(eq_curve)
        if series.bars:
            start_dates.append(series.bars[0].timestamp)
            end_dates.append(series.bars[-1].timestamp)

    if not combined_equity_curves:
        raise InsufficientDataForBacktestError(
            "No valid market data series available for backtest evaluation."
        )

    # Sort trades chronologically by entry timestamp
    sorted_trades = tuple(sorted(all_trades, key=lambda t: t.entry_timestamp))

    # Aggregate equity curve across symbols
    min_len = min(len(curve) for curve in combined_equity_curves)
    aggregated_equity_curve = tuple(
        round(sum(curve[i] for curve in combined_equity_curves), 4) for i in range(min_len)
    )

    metrics = calculate_performance_metrics(
        trades=sorted_trades,
        equity_curve=aggregated_equity_curve,
        initial_capital=initial_capital,
    )

    sim_start = min(start_dates) if start_dates else executed_at
    sim_end = max(end_dates) if end_dates else executed_at
    if split is not None and is_in_sample:
        sim_start = max(sim_start, split.start_date)
        sim_end = min(sim_end, split.in_sample_end_date)

    return BacktestResult(
        backtest_id=backtest_id,
        experiment_id=experiment_id,
        config_version="1.0.0",
        parameters=config.model_dump(),
        metrics=metrics,
        trades=sorted_trades,
        equity_curve=aggregated_equity_curve,
        start_date=sim_start,
        end_date=sim_end,
        executed_at=executed_at,
    )


def evaluate_experiment_backtest(
    experiment: ExperimentDesign,
    base_config: StrategyConfig,
    market_data: BarSeries | dict[str, BarSeries],
    initial_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.01,
    executed_at: datetime | None = None,
) -> tuple[BacktestComparison, BacktestReceipt]:
    """Run in-sample backtest comparing baseline configuration against experiment variations.

    Args:
        experiment: Immutable ExperimentDesign to evaluate.
        base_config: Baseline StrategyConfig.
        market_data: Historical market data series.
        initial_capital: Simulation initial capital.
        risk_per_trade_pct: Single-trade risk budget.
        executed_at: Echoed evaluation timestamp anchor.

    Returns:
        Tuple of (BacktestComparison, BacktestReceipt).

    Raises:
        InvalidBacktestConfigurationError: If parameter variation overrides are invalid.
        InSampleViolationError: If in-sample split rules are breached.
    """
    if not isinstance(experiment, ExperimentDesign):
        raise UnknownScientistInputError(
            f"experiment must be an ExperimentDesign instance, got {type(experiment)}"
        )
    if not isinstance(base_config, StrategyConfig):
        raise InvalidBacktestConfigurationError(
            f"base_config must be a StrategyConfig instance, got {type(base_config)}"
        )
    if executed_at is None or not isinstance(executed_at, datetime):
        raise UnknownScientistInputError(f"Invalid executed_at timestamp: {executed_at}")

    # 1. Run Baseline In-Sample Backtest
    baseline_id = f"BT-BASE-{experiment.experiment_id}"
    baseline_result = run_backtest(
        backtest_id=baseline_id,
        experiment_id=experiment.experiment_id,
        config=base_config,
        market_data=market_data,
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        split=experiment.dataset_split,
        is_in_sample=True,
        executed_at=executed_at,
    )

    # 2. Construct Candidate Configuration with Parameter Variations
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
            f"Failed to apply parameter variations to StrategyConfig: {e}"
        ) from e

    # 3. Run Candidate In-Sample Backtest
    candidate_id = f"BT-CAND-{experiment.experiment_id}"
    candidate_result = run_backtest(
        backtest_id=candidate_id,
        experiment_id=experiment.experiment_id,
        config=candidate_config,
        market_data=market_data,
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        split=experiment.dataset_split,
        is_in_sample=True,
        executed_at=executed_at,
    )

    # 4. Compute Performance Deltas
    base_m = baseline_result.metrics
    cand_m = candidate_result.metrics

    deltas: dict[str, float] = {
        "sharpe_ratio": round(cand_m.sharpe_ratio - base_m.sharpe_ratio, 4),
        "profit_factor": round(cand_m.profit_factor - base_m.profit_factor, 4),
        "win_rate": round(cand_m.win_rate - base_m.win_rate, 4),
        "max_drawdown": round(cand_m.max_drawdown - base_m.max_drawdown, 4),
        "total_pnl": round(cand_m.total_pnl - base_m.total_pnl, 4),
        "net_return_pct": round(cand_m.net_return_pct - base_m.net_return_pct, 4),
    }

    # Evaluate Target Metric Improvements
    target_improved: dict[str, bool] = {}
    for target in experiment.target_metrics:
        if target == "max_drawdown":
            # For drawdown, smaller is better
            target_improved[target] = cand_m.max_drawdown <= base_m.max_drawdown
        elif hasattr(cand_m, target) and hasattr(base_m, target):
            cand_val = getattr(cand_m, target)
            base_val = getattr(base_m, target)
            target_improved[target] = cand_val >= base_val
        else:
            target_improved[target] = False

    # Hypothesis is considered supported if the majority of target metrics improved or matched
    improved_count = sum(1 for imp in target_improved.values() if imp)
    hypothesis_supported = (
        improved_count >= (len(target_improved) / 2) if target_improved else False
    )

    summary = (
        f"Experiment {experiment.experiment_id} evaluated against baseline. "
        f"Target metrics improved: {improved_count}/{len(target_improved)}. "
        f"Sharpe: {base_m.sharpe_ratio:.2f} -> {cand_m.sharpe_ratio:.2f} "
        f"(delta {deltas['sharpe_ratio']:+.2f}). "
        f"Max DD: {base_m.max_drawdown:.2%} -> {cand_m.max_drawdown:.2%} "
        f"(delta {deltas['max_drawdown']:+.2%}). "
        f"Hypothesis supported: {hypothesis_supported}."
    )

    comparison = BacktestComparison(
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        baseline_result=baseline_result,
        candidate_result=candidate_result,
        metric_deltas=deltas,
        target_metrics_improved=target_improved,
        hypothesis_supported=hypothesis_supported,
        comparison_summary=summary,
    )

    # 5. Generate Deterministic Audit Receipt (INV-001, INV-006)
    checksum = compute_backtest_checksum(
        backtest_id=candidate_id,
        experiment_id=experiment.experiment_id,
        metrics=cand_m,
        parameters=candidate_config.model_dump(),
    )

    receipt = BacktestReceipt(
        receipt_id=f"RCPT-BT-{experiment.experiment_id}",
        experiment_id=experiment.experiment_id,
        backtest_id=candidate_id,
        action="IN_SAMPLE_BACKTEST_EVALUATED",
        timestamp=executed_at,
        checksum=checksum,
        details=summary,
    )

    return comparison, receipt


class BacktestEngine:
    """Deterministic Scientist Backtesting Engine with strict isolation (INV-014)."""

    def __init__(self, base_config: StrategyConfig) -> None:
        if not isinstance(base_config, StrategyConfig):
            raise InvalidBacktestConfigurationError("base_config must be a valid StrategyConfig")
        self.base_config = base_config

    def evaluate_in_sample(
        self,
        experiment: ExperimentDesign,
        market_data: BarSeries | dict[str, BarSeries],
        initial_capital: float = 100_000.0,
        risk_per_trade_pct: float = 0.01,
        executed_at: datetime | None = None,
    ) -> tuple[BacktestComparison, BacktestReceipt]:
        """Execute in-sample experiment backtest."""
        return evaluate_experiment_backtest(
            experiment=experiment,
            base_config=self.base_config,
            market_data=market_data,
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            executed_at=executed_at,
        )

    def execute_live_order(self, *args: Any, **kwargs: Any) -> None:
        """Strictly forbidden: Scientist tools cannot execute live trades (INV-014)."""
        raise ScientistExecutionBreachError(
            "BacktestEngine cannot submit or route live production orders (INV-014 violation)."
        )

    def cancel_live_order(self, *args: Any, **kwargs: Any) -> None:
        """Strictly forbidden: Scientist tools cannot cancel live trades (INV-014)."""
        raise ScientistExecutionBreachError(
            "BacktestEngine cannot cancel live production orders (INV-014 violation)."
        )
