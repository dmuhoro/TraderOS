from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.swing_detection import SwingDetectionService


def _c_hl(high: float, low: float, idx: int = 0) -> Candle:
    mid = (high + low) / 2
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(mid)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(mid)),
            volume=Decimal(1000),
        ),
        timestamp=datetime(2024, 1, 1 + idx, tzinfo=UTC),
        timeframe=Timeframe.DAY_1,
    )


class TestSwingDetection:
    def test_empty(self) -> None:
        result = SwingDetectionService.detect_swings([], 2)
        assert result.highs == []
        assert result.lows == []

    def test_swing_high_window_2(self) -> None:
        candles = [
            _c_hl(10, 1, idx=0),
            _c_hl(15, 2, idx=1),
            _c_hl(20, 3, idx=2),  # swing high: 20 > 10,15,12,8
            _c_hl(12, 4, idx=3),
            _c_hl(8, 5, idx=4),
        ]
        result = SwingDetectionService.detect_swings(candles, 2)
        assert len(result.highs) == 1
        assert result.highs[0].value == 20.0
        assert result.highs[0].name == "swing_high"
        assert result.lows == []

    def test_swing_low_window_2(self) -> None:
        candles = [
            _c_hl(20, 20, idx=0),
            _c_hl(18, 15, idx=1),
            _c_hl(16, 10, idx=2),
            _c_hl(15, 12, idx=3),
            _c_hl(14, 5, idx=4),  # swing low: 5 < 10,12,12,15
            _c_hl(16, 12, idx=5),
            _c_hl(18, 15, idx=6),
        ]
        result = SwingDetectionService.detect_swings(candles, 2)
        assert len(result.lows) == 1
        assert result.lows[0].value == 5.0
        assert result.lows[0].name == "swing_low"

    def test_not_enough_data(self) -> None:
        candles = [_c_hl(10, 1, idx=i) for i in range(4)]
        result = SwingDetectionService.detect_swings(candles, 2)
        assert result.highs == []
        assert result.lows == []

    def test_multiple_swings(self) -> None:
        candles = [
            _c_hl(10, 9, idx=0),
            _c_hl(20, 8, idx=1),
            _c_hl(30, 3, idx=2),  # swing high: 30 > 10,20,4,8
            _c_hl(4, 1, idx=3),  # swing low: 1 < 8,3,2,9
            _c_hl(8, 2, idx=4),
            _c_hl(15, 9, idx=5),
        ]
        result = SwingDetectionService.detect_swings(candles, 2)
        assert len(result.highs) == 1
        assert len(result.lows) == 1
        assert result.highs[0].value == 30.0
        assert result.lows[0].value == 1.0


class TestRecentSwings:
    def test_get_recent(self) -> None:
        candles = [
            _c_hl(10, 9, idx=0),
            _c_hl(20, 8, idx=1),
            _c_hl(30, 3, idx=2),  # swing high
            _c_hl(4, 1, idx=3),  # swing low
            _c_hl(8, 2, idx=4),
            _c_hl(15, 9, idx=5),
        ]
        result = SwingDetectionService.get_recent_swings(candles, 2, count=5)
        assert len(result.highs) == 1
        assert len(result.lows) == 1
