from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.breakout_detection import BreakoutDetectionService


def _c(close: float, idx: int = 0) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(close + 1)),
            low=Decimal(str(close - 1)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        ),
        timestamp=datetime(2024, 1, 1) + timedelta(days=idx),
        timeframe=Timeframe.DAY_1,
    )


class TestBreakoutDetection:
    def test_empty_candles(self) -> None:
        assert BreakoutDetectionService.analyze([], 0.001, 2.0, 10, 20) == []

    def test_not_enough_data(self) -> None:
        candles = [_c(100, idx=i) for i in range(5)]
        assert BreakoutDetectionService.analyze(candles, 0.001, 2.0, 10, 20) == []

    def test_consolidation_detected(self) -> None:
        # Constant prices = very low volatility = consolidation
        candles = [_c(100, idx=i) for i in range(40)]
        events = BreakoutDetectionService.analyze(
            candles,
            vol_threshold=0.01,
            sensitivity=2.0,
            vol_window=5,
            ma_window=10,
        )
        consolidation_events = [e for e in events if e.event_type == "Consolidation"]
        assert len(consolidation_events) >= 1

    def test_breakout_detected(self) -> None:
        # Calm then spike → breakout
        candles = [_c(100, idx=i) for i in range(20)]
        # Add a volatile spike
        for i in range(20, 40):
            close = 100 + (i - 20) * 10
            candles.append(_c(close, idx=i))
        events = BreakoutDetectionService.analyze(
            candles,
            vol_threshold=0.005,
            sensitivity=1.5,
            vol_window=5,
            ma_window=10,
        )
        breakout_events = [e for e in events if e.event_type == "Breakout"]
        assert len(breakout_events) >= 1

    def test_event_metadata(self) -> None:
        candles = [_c(100, idx=i) for i in range(40)]
        events = BreakoutDetectionService.analyze(
            candles,
            vol_threshold=0.01,
            sensitivity=2.0,
            vol_window=5,
            ma_window=10,
        )
        if events:
            e = events[0]
            assert e.event_type in ("Breakout", "Consolidation")
            assert isinstance(e.description, str)
