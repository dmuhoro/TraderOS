from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.correlation_service import CorrelationService


def _c(close: float, ts: datetime) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(close + 1)),
            low=Decimal(str(close - 1)),
            close=Decimal(str(close)),
            volume=Decimal(1000),
        ),
        timestamp=ts,
        timeframe=Timeframe.DAY_1,
    )


def _ts(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=UTC)


class TestCorrelationService:
    def test_empty_series(self) -> None:
        a = [_c(10, _ts(1)), _c(20, _ts(2))]
        assert CorrelationService.compute_correlation([], a) is None
        assert CorrelationService.compute_correlation(a, []) is None

    def test_insufficient_data(self) -> None:
        a = [_c(10, _ts(1))]
        b = [_c(20, _ts(1))]
        assert CorrelationService.compute_correlation(a, b) is None

    def test_no_overlap(self) -> None:
        a = [_c(10, _ts(1)), _c(20, _ts(2))]
        b = [_c(30, _ts(3)), _c(40, _ts(4))]
        assert CorrelationService.compute_correlation(a, b) is None

    def test_two_overlapping_timestamps_insufficient_returns(self) -> None:
        a = [_c(10, _ts(1)), _c(20, _ts(2))]
        b = [_c(30, _ts(1)), _c(40, _ts(2))]
        assert CorrelationService.compute_correlation(a, b) is None

    def test_perfect_positive(self) -> None:
        a = [_c(10, _ts(1)), _c(11, _ts(2)), _c(12, _ts(3))]
        b = [_c(100, _ts(1)), _c(110, _ts(2)), _c(120, _ts(3))]
        result = CorrelationService.compute_correlation(a, b)
        assert result is not None
        assert abs(result.value - 1.0) < 0.001
        assert result.n_periods == 2

    def test_perfect_negative(self) -> None:
        dt = [_ts(1), _ts(2), _ts(3)]
        a = [_c(10, dt[0]), _c(12, dt[1]), _c(9, dt[2])]
        b = [_c(100, dt[0]), _c(80, dt[1]), _c(110, dt[2])]
        result = CorrelationService.compute_correlation(a, b)
        assert result is not None
        assert abs(result.value - (-1.0)) < 0.001

    def test_constant_prices(self) -> None:
        a = [_c(10, _ts(1)), _c(10, _ts(2)), _c(10, _ts(3))]
        b = [_c(20, _ts(1)), _c(20, _ts(2)), _c(20, _ts(3))]
        assert CorrelationService.compute_correlation(a, b) is None

    def test_partial_overlap(self) -> None:
        # 3 common timestamps = 2 return periods
        a = [_c(10, _ts(1)), _c(11, _ts(2)), _c(12, _ts(3)), _c(13, _ts(4))]
        b = [_c(100, _ts(2)), _c(110, _ts(3)), _c(120, _ts(4)), _c(130, _ts(5))]
        result = CorrelationService.compute_correlation(a, b)
        assert result is not None
        assert abs(result.value - 1.0) < 0.001
        assert result.n_periods == 2

    def test_known_correlation(self) -> None:
        a = [_c(1, _ts(1)), _c(2, _ts(2)), _c(3, _ts(3)), _c(5, _ts(4))]
        b = [_c(10, _ts(1)), _c(30, _ts(2)), _c(20, _ts(3)), _c(40, _ts(4))]
        result = CorrelationService.compute_correlation(a, b)
        assert result is not None
        assert result.n_periods == 3
        assert result.value > 0


class TestCorrelationMatrix:
    def test_matrix(self) -> None:
        # C is constructed so returns are exact negatives of A
        series = {
            "A": [_c(10, _ts(1)), _c(11, _ts(2)), _c(12, _ts(3))],
            "B": [_c(100, _ts(1)), _c(110, _ts(2)), _c(120, _ts(3))],
            "C": [_c(100, _ts(1)), _c(90, _ts(2)), _c(81.82, _ts(3))],
        }
        matrix = CorrelationService.compute_correlation_matrix(series)
        assert ("A", "B") in matrix
        assert ("A", "C") in matrix
        assert ("B", "C") in matrix
        assert abs(matrix[("A", "B")] - 1.0) < 0.001
        assert abs(matrix[("A", "C")] - (-1.0)) < 0.05

    def test_empty_series(self) -> None:
        assert CorrelationService.compute_correlation_matrix({}) == {}

    def test_insufficient_pairs(self) -> None:
        series = {"A": [_c(10, _ts(1))]}
        assert CorrelationService.compute_correlation_matrix(series) == {}
