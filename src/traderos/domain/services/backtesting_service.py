from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

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
        start_time = time.monotonic()
        cash = self.initial_capital
        position_qty = 0.0
        equity_curve: list[tuple[datetime, float]] = []
        steps: list[BacktestStep] = []

        for i, candle in enumerate(candles):
            if time.monotonic() - start_time > max_duration_seconds:
                remaining = len(candles) - i
                raise TimeoutError(
                    f"Backtest exceeded {max_duration_seconds}s ({remaining} candles remaining)"
                )
            indicators: dict[str, float] = {
                "close": float(candle.ohlcv.close),
                "high": float(candle.ohlcv.high),
                "low": float(candle.ohlcv.low),
                "volume": float(candle.ohlcv.volume),
                "sma_20": 0.0,
                "atr_14": 0.0,
            }
            if i >= 1:
                prev_highs = [float(c.ohlcv.high) for c in candles[max(0, i - 19) : i + 1]]
                prev_lows = [float(c.ohlcv.low) for c in candles[max(0, i - 19) : i + 1]]
                prev_closes = [float(c.ohlcv.close) for c in candles[max(0, i - 19) : i + 1]]
                sma = sum(prev_closes) / len(prev_closes)
                indicators["sma_20"] = sma
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
                fill_result = self.execution.process_market_order(order, float(candle.ohlcv.close))
                if fill_result.filled:
                    fill_price = fill_result.fill_price
                    if side == "buy":
                        position_qty += fill_result.fill_quantity
                        cash -= fill_result.fill_quantity * fill_result.fill_price
                    else:
                        position_qty -= fill_result.fill_quantity
                        cash += fill_result.fill_quantity * fill_result.fill_price

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

        metrics = self.compute_metrics(equity_curve)
        result = BacktestResult(
            strategy_id=uuid.uuid4(),
            market_id=market_id,
            metrics=metrics,
            equity_curve=EquityCurve(tuple(equity_curve)),
            period_start=candles[0].timestamp,
            period_end=candles[-1].timestamp,
        )
        return result, steps
