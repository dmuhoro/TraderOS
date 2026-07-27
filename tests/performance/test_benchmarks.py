from __future__ import annotations

import time
import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import MovingAverageTrend


class TestBenchmarks:
    def _candles(self, n: int) -> list[Candle]:
        mid = uuid.uuid4()
        return [
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(str(100 + i)),
                    high=Decimal(str(101 + i)),
                    low=Decimal(str(99 + i)),
                    close=Decimal(str(100 + i)),
                    volume=Decimal(1000),
                ),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                timeframe=Timeframe.DAY_1,
            )
            for i in range(n)
        ]

    def test_backtest_1000_candles_under_1s(self) -> None:
        strategy = MovingAverageTrend()
        candles = self._candles(1000)
        svc = BacktestingService(execution=ExecutionService())
        start = time.perf_counter()
        svc.run(strategy, candles, uuid.uuid4())
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Backtest took {elapsed:.2f}s"

    def test_execution_1000_orders_under_100ms(self) -> None:
        svc = ExecutionService()
        mid = uuid.uuid4()
        orders = [svc.create_market_order(mid, "buy", 10.0) for _ in range(1000)]
        start = time.perf_counter()
        for o in orders:
            svc.process_market_order(o, 100.0)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"1000 orders took {elapsed:.2f}s"
