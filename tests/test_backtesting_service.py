from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase


class AlwaysBuy(StrategyBase):
    def evaluate(self, state: MarketState) -> SignalResult | None:
        return SignalResult("long", 0.5, {})


class NeverTrade(StrategyBase):
    def evaluate(self, state: MarketState) -> SignalResult | None:
        return None


class TestBacktestingService:
    def _candles(self, n: int, start_price: float = 100.0) -> list[Candle]:
        result: list[Candle] = []
        mid = uuid.uuid4()
        for i in range(n):
            result.append(
                Candle(
                    market_id=mid,
                    ohlcv=OHLCV(
                        open=Decimal(str(start_price + i)),
                        high=Decimal(str(start_price + i + 1)),
                        low=Decimal(str(start_price + i - 1)),
                        close=Decimal(str(start_price + i)),
                        volume=Decimal(1000),
                    ),
                    timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                    timeframe=Timeframe.DAY_1,
                )
            )
        return result

    def test_run_with_always_buy(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        candles = self._candles(30, 100.0)
        result, steps = svc.run(AlwaysBuy(), candles, uuid.uuid4())
        assert result.metrics.total_return != 0
        assert len(steps) == 30

    def test_run_with_never_trade(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        candles = self._candles(30, 100.0)
        result, steps = svc.run(NeverTrade(), candles, uuid.uuid4())
        assert result.metrics.total_return == 0.0
        assert all(s.order is None for s in steps)

    def test_compute_metrics_empty(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        metrics = svc.compute_metrics([])
        assert metrics.total_return == 0.0

    def test_compute_metrics_single(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        metrics = svc.compute_metrics([(datetime.now(UTC), 100.0)])
        assert metrics.total_return == 0.0

    def test_compute_metrics_with_gains(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        now = datetime.now(UTC)
        curve = [(now, 100.0), (now, 105.0), (now, 110.0), (now, 108.0)]
        metrics = svc.compute_metrics(curve)
        assert 0.0 < metrics.total_return < 0.1
        assert isinstance(metrics.sharpe_ratio, float)

    def test_run_raises_timeout_when_duration_exceeded(self, monkeypatch) -> None:
        ticks = iter([0.0, 301.0])
        monkeypatch.setattr(
            "traderos.domain.services.backtesting_service.time.monotonic", lambda: next(ticks)
        )
        svc = BacktestingService(execution=ExecutionService())
        candles = self._candles(30)
        try:
            svc.run(AlwaysBuy(), candles, uuid.uuid4(), max_duration_seconds=300)
        except TimeoutError as exc:
            assert "exceeded" in str(exc)
        else:
            raise AssertionError("expected TimeoutError")
