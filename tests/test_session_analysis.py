from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.session_analysis import SessionAnalysisService


def _c(close: float, hour: int, day: int = 1) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(close + 2)),
            low=Decimal(str(close - 2)),
            close=Decimal(str(close)),
            volume=Decimal(1000),
        ),
        timestamp=datetime(2024, 1, day, hour, tzinfo=UTC),
        timeframe=Timeframe.HOUR_1,
    )


class TestSessionAssignment:
    def test_empty(self) -> None:
        assert SessionAnalysisService.assign_sessions([], {"London": [7, 16]}) == {}

    def test_simple_session(self) -> None:
        candles = [_c(100, hour=10, day=1)]
        sessions = {"London": [7, 16]}
        result = SessionAnalysisService.assign_sessions(candles, sessions)
        assert result[candles[0].timestamp] == "London"

    def test_fallback_to_other(self) -> None:
        candles = [_c(100, hour=3, day=1)]
        sessions = {"London": [7, 16]}
        result = SessionAnalysisService.assign_sessions(candles, sessions)
        assert result[candles[0].timestamp] == "Other"

    def test_overnight_session(self) -> None:
        candles = [_c(100, hour=23, day=1)]
        sessions = {"Asia": [20, 4]}
        result = SessionAnalysisService.assign_sessions(candles, sessions)
        assert result[candles[0].timestamp] == "Asia"


class TestSessionStats:
    def test_empty(self) -> None:
        assert SessionAnalysisService.compute_session_stats([], {"London": [7, 16]}) == []

    def test_stats_computed(self) -> None:
        candles = [
            _c(100, hour=8, day=1),
            _c(102, hour=9, day=1),
            _c(101, hour=10, day=1),
        ]
        sessions = {"London": [7, 16]}
        stats = SessionAnalysisService.compute_session_stats(candles, sessions)
        assert len(stats) == 1
        s = stats[0]
        assert s.session == "London"
        assert s.bar_count == 3
        # high = max(102, 104, 103) = 104, low = min(98, 100, 99) = 98
        assert abs(s.range_size - 6.0) < 0.01

    def test_two_days_two_sessions(self) -> None:
        candles = [
            _c(100, hour=3, day=1),
            _c(101, hour=3, day=2),
            _c(102, hour=10, day=1),
            _c(103, hour=10, day=2),
        ]
        sessions = {"Asia": [20, 4], "London": [7, 16]}
        stats = SessionAnalysisService.compute_session_stats(candles, sessions)
        assert len(stats) == 4  # 2 sessions × 2 days
