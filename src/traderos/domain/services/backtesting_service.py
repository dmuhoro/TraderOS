from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple
from typing import TypedDict

from traderos.domain.entities import OHLCV
from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Candle
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Metrics
from traderos.domain.entities import Timeframe
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.execution_service import Order
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import StrategyBase


class BacktestStep(NamedTuple):
    timestamp: datetime
    equity: float
    order: Order | None
    fill_price: float | None


class WalkForwardReport(TypedDict):
    fold_returns: list[float]
    sharpe: list[float]
    max_drawdowns: list[float]
    trades: list[int]
    mean_fold_return: float
    positive_folds: float
    mean_sharpe: float
    mean_max_drawdown: float


def synthetic_candles(
    count: int = 50,
    start_price: float = 100.0,
    market_id: uuid.UUID | None = None,
) -> list[Candle]:
    """Deterministic upward-trend candles for operator compare/backtest runs.

    Shared by the API, CLI and strategy management so a backtest has a stable,
    reproducible market without live connectivity (synthetic-only, unchanged
    from the pre-existing behaviour).
    """
    mid = market_id or uuid.uuid4()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            market_id=mid,
            ohlcv=OHLCV(
                open=Decimal(str(round(start_price + i, 4))),
                high=Decimal(str(round(start_price + i + 1, 4))),
                low=Decimal(str(round(start_price + i - 1, 4))),
                close=Decimal(str(round(start_price + i, 4))),
                volume=Decimal(1000),
            ),
            timestamp=start,
            timeframe=Timeframe.DAY_1,
        )
        for i in range(count)
    ]


@dataclass
class BacktestingService:
    execution: ExecutionService
    initial_capital: float = float(os.getenv("DEFAULT_CASH", "10000.0"))

    def compute_metrics(
        self,
        equity_curve: list[tuple[datetime, float]],
    ) -> Metrics:
        if len(equity_curve) < 2:
            return Metrics()

        values = [v for _, v in equity_curve]
        returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]

        total_return = (values[-1] - values[0]) / values[0]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        n = len(returns)
        std = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / (n - 1)) if n > 1 else 0.0

        sharpe = (avg_return / std * math.sqrt(252)) if std > 0 else 0.0

        negative = [r for r in returns if r < 0]
        downside_std = (
            math.sqrt(sum(r**2 for r in negative) / (n - 1)) if negative and n > 1 else 0.0
        )
        sortino = (avg_return / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

        peak = values[0]
        max_dd = 0.0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

        calmar = total_return / max_dd if max_dd > 0 else 0.0

        profit = sum(r for r in returns if r > 0)
        loss = abs(sum(r for r in returns if r < 0))
        profit_factor = profit / loss if loss > 0 else float("inf")
        win_rate = len([r for r in returns if r > 0]) / len(returns) if returns else 0.0

        return Metrics(
            total_return=total_return,
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            calmar_ratio=round(calmar, 4),
            max_drawdown=round(max_dd, 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else 0.0,
            total_trades=0,
            expectancy=round(avg_return, 4),
            recovery_factor=round(total_return / max_dd, 4) if max_dd > 0 else 0.0,
        )

    def run(
        self,
        strategy: StrategyBase,
        candles: list[Candle],
        market_id: uuid.UUID,
        max_duration_seconds: int = 300,
    ) -> tuple[BacktestResult, list[BacktestStep]]:
        """Cost-realistic, latency-aware backtest.

        Fills happen on the **next** candle's open after a signal (no
        same-bar look-ahead), and every fill pays side-aware slippage plus the
        configured fee. A signal on the final candle is never filled — it is
        dropped, exactly like a real market order that never executes.
        """
        start_time = time.monotonic()
        cash = self.initial_capital
        position_qty = 0.0
        equity_curve: list[tuple[datetime, float]] = []
        steps: list[BacktestStep] = []
        filled_orders = 0
        pending: list[Order] = []

        for i, candle in enumerate(candles):
            if time.monotonic() - start_time > max_duration_seconds:
                remaining = len(candles) - i
                raise TimeoutError(
                    f"Backtest exceeded {max_duration_seconds}s ({remaining} candles remaining)"
                )
            executed_this_bar: list[float] = []
            open_price = float(candle.ohlcv.open)
            for order in pending:
                fill_result = self.execution.process_market_order(order, open_price)
                if fill_result.filled:
                    filled_orders += 1
                    executed_this_bar.append(fill_result.fill_price)
                    if order.side == "buy":
                        position_qty += fill_result.fill_quantity
                        cash -= fill_result.fill_quantity * fill_result.fill_price
                    else:
                        position_qty -= fill_result.fill_quantity
                        cash += fill_result.fill_quantity * fill_result.fill_price
                    cash -= fill_result.fee
            pending = []

            indicators: dict[str, float] = {
                "close": float(candle.ohlcv.close),
                "high": float(candle.ohlcv.high),
                "low": float(candle.ohlcv.low),
                "volume": float(candle.ohlcv.volume),
                "sma_20": 0.0,
                "sma_50": 0.0,
                "bb_upper_20": 0.0,
                "bb_mid_20": 0.0,
                "bb_lower_20": 0.0,
                "atr_14": 0.0,
            }
            if i >= 1:
                prev_highs = [float(c.ohlcv.high) for c in candles[max(0, i - 49) : i + 1]]
                prev_lows = [float(c.ohlcv.low) for c in candles[max(0, i - 49) : i + 1]]
                prev_closes = [float(c.ohlcv.close) for c in candles[max(0, i - 19) : i + 1]]
                sma20 = sum(prev_closes) / len(prev_closes)
                indicators["sma_20"] = sma20
                indicators["bb_mid_20"] = sma20
                if len(prev_closes) >= 2:
                    variance = sum((c - sma20) ** 2 for c in prev_closes) / len(prev_closes)
                    std = variance**0.5
                    indicators["bb_upper_20"] = sma20 + 2 * std
                    indicators["bb_lower_20"] = sma20 - 2 * std
                prev_closes_50 = [float(c.ohlcv.close) for c in candles[max(0, i - 49) : i + 1]]
                if len(prev_closes_50) >= 1:
                    indicators["sma_50"] = sum(prev_closes_50) / len(prev_closes_50)
                if len(prev_closes) >= 14:
                    tr_values = []
                    for j in range(len(prev_closes) - 1):
                        hl = prev_highs[j] - prev_lows[j]
                        hpc = abs(prev_highs[j] - prev_closes[j - 1]) if j > 0 else 0
                        lpc = abs(prev_lows[j] - prev_closes[j - 1]) if j > 0 else 0
                        tr_values.append(max(hl, hpc, lpc))
                    indicators["atr_14"] = sum(tr_values) / len(tr_values)

            state = MarketState(
                timestamp=candle.timestamp,
                candles=candles[: i + 1],
                indicators=indicators,
            )

            signal = strategy.evaluate(state)
            order: Order | None = None
            fill_price: float | None = None
            if signal is not None:
                side = "buy" if signal.direction == "long" else "sell"
                qty = signal.confidence * 10
                order = self.execution.create_market_order(market_id, side, qty)
                pending.append(order)
            if executed_this_bar:
                fill_price = executed_this_bar[0]

            current_value = cash + position_qty * float(candle.ohlcv.close)
            equity_curve.append((candle.timestamp, current_value))
            steps.append(
                BacktestStep(
                    timestamp=candle.timestamp,
                    equity=current_value,
                    order=order,
                    fill_price=fill_price,
                )
            )

        metrics = replace(self.compute_metrics(equity_curve), total_trades=filled_orders)
        result = BacktestResult(
            strategy_id=uuid.uuid4(),
            market_id=market_id,
            metrics=metrics,
            equity_curve=EquityCurve(tuple(equity_curve)),
            period_start=candles[0].timestamp,
            period_end=candles[-1].timestamp,
        )
        return result, steps

    def walk_forward(
        self,
        strategy: StrategyBase,
        candles: list[Candle],
        market_id: uuid.UUID,
        n_splits: int = 5,
        warmup: int = 50,
        max_duration_seconds: int = 300,
    ) -> WalkForwardReport:
        """Anchored, rolling out-of-sample evaluation.

        The series is split into ``n_splits`` contiguous folds. Each fold is
        evaluated with a ``warmup``-candle prefix so indicator warm-up is not
        an artifact (a strategy may need 20–50 bars before it can fire), but
        only the fold itself counts toward the metrics. Folds never leak
        future data into the evaluated region, and the warmup region is never
        scored. An edge that only exists in-sample is not an edge.
        """
        if len(candles) < n_splits * 2:
            raise ValueError(
                f"walk_forward needs at least {n_splits * 2} candles, got {len(candles)}"
            )
        totals: list[float] = []
        sharpe: list[float] = []
        draws: list[float] = []
        trades: list[int] = []
        fold_size = len(candles) // n_splits
        for fold in range(n_splits):
            lo = fold * fold_size
            hi = lo + fold_size if fold < n_splits - 1 else len(candles)
            fold_candles = candles[lo:hi]
            window = candles[max(0, lo - warmup) : hi]
            _, steps = self.run(
                strategy,
                window,
                market_id,
                max_duration_seconds=max_duration_seconds,
            )
            fold_start = fold_candles[0].timestamp
            fold_steps = [s for s in steps if s.timestamp >= fold_start]
            sub_metrics = self.compute_metrics([(s.timestamp, s.equity) for s in fold_steps])
            fold_trades = len([s for s in fold_steps if s.fill_price is not None])
            totals.append(sub_metrics.total_return)
            sharpe.append(sub_metrics.sharpe_ratio)
            draws.append(sub_metrics.max_drawdown)
            trades.append(fold_trades)
        return {
            "fold_returns": totals,
            "sharpe": sharpe,
            "max_drawdowns": draws,
            "trades": trades,
            "mean_fold_return": sum(totals) / len(totals) if totals else 0.0,
            "positive_folds": float(len([t for t in totals if t > 0])),
            "mean_sharpe": sum(sharpe) / len(sharpe) if sharpe else 0.0,
            "mean_max_drawdown": sum(draws) / len(draws) if draws else 0.0,
        }
