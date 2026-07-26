from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.sweep_detection import SweepDetectionService


def _c(high: float, low: float, close: float, idx: int = 0) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        ),
        timestamp=datetime(2024, 1, 1 + idx),
        timeframe=Timeframe.DAY_1,
    )


class TestSweepDetection:
    def test_empty_candles(self) -> None:
        assert SweepDetectionService.detect_sweeps([], [100], [90]) == []

    def test_no_swing_data(self) -> None:
        candles = [_c(110, 80, 105, idx=0)]
        assert SweepDetectionService.detect_sweeps(candles, [], []) == []

    def test_bullish_sweep(self) -> None:
        candle = _c(95, 85, 92, idx=0)  # low=85 < last_low=90, close=92 > 90
        sweeps = SweepDetectionService.detect_sweeps([candle], [], [90])
        assert len(sweeps) == 1
        assert sweeps[0].event_type == "Liquidity Sweep (Bullish)"
        assert "below previous low" in sweeps[0].description

    def test_bearish_sweep(self) -> None:
        candle = _c(115, 100, 105, idx=0)  # high=115 > last_high=110, close=105 < 110
        sweeps = SweepDetectionService.detect_sweeps([candle], [110], [])
        assert len(sweeps) == 1
        assert sweeps[0].event_type == "Liquidity Sweep (Bearish)"
        assert "above previous high" in sweeps[0].description

    def test_no_sweep_when_close_beyond(self) -> None:
        # Bearish sweep condition: high > last_high AND close < last_high
        # If close is also > last_high, it's a breakout, not a sweep
        candle = _c(115, 100, 112, idx=0)  # close=112 > last_high=110 → breakout
        sweeps = SweepDetectionService.detect_sweeps([candle], [110], [])
        assert sweeps == []

    def test_both_sweep_types(self) -> None:
        candle = _c(115, 85, 105, idx=0)
        sweeps = SweepDetectionService.detect_sweeps([candle], [110], [90])
        assert len(sweeps) == 2
