from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.regime_detection import Regime
from traderos.domain.services.regime_detection import RegimeDetectionService


def _c(close: float, idx: int = 0) -> Candle:
    return Candle(
        market_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ohlcv=OHLCV(
            open=Decimal(str(close)),
            high=Decimal(str(close + 5)),
            low=Decimal(str(close - 5)),
            close=Decimal(str(close)),
            volume=Decimal(1000),
        ),
        timestamp=datetime(2024, 1, 1 + idx, tzinfo=UTC),
        timeframe=Timeframe.DAY_1,
    )


class TestRegimeEnum:
    def test_values(self) -> None:
        assert list(Regime) == [
            Regime.TRENDING_BULLISH,
            Regime.TRENDING_BEARISH,
            Regime.RANGING,
            Regime.HIGH_VOLATILITY,
            Regime.UNKNOWN,
        ]

    def test_str(self) -> None:
        assert str(Regime.TRENDING_BULLISH) == "trending_bullish"


class TestRegimeDetection:
    def test_empty_candles(self) -> None:
        assert RegimeDetectionService.detect([], 3, 5) == []

    def test_not_enough_data(self) -> None:
        candles = [_c(10, idx=i) for i in range(4)]  # need 5 for slow_window
        assert RegimeDetectionService.detect(candles, 3, 5) == []

    def test_bullish_regime(self) -> None:
        candles = [_c(v * 20, idx=i) for i, v in enumerate([1, 2, 3, 4, 5, 6, 7])]
        results = RegimeDetectionService.detect(candles, 3, 5)
        assert len(results) == 3  # i=4,5,6 have both SMA3 and SMA5
        for r in results:
            assert r.regime == Regime.TRENDING_BULLISH, f"Failed at {r.timestamp}"

    def test_bearish_regime(self) -> None:
        candles = [_c(v * 20, idx=i) for i, v in enumerate([7, 6, 5, 4, 3, 2, 1])]
        results = RegimeDetectionService.detect(candles, 3, 5)
        assert len(results) == 3
        for r in results:
            assert r.regime == Regime.TRENDING_BEARISH, f"Failed at {r.timestamp}"

    def test_ranging_regime(self) -> None:
        prices = [100, 101, 99, 100, 101, 99, 100]
        candles = [_c(p, idx=i) for i, p in enumerate(prices)]
        results = RegimeDetectionService.detect(candles, 3, 5)
        assert len(results) == 3
        for r in results:
            assert r.regime == Regime.RANGING, f"Failed at {r.timestamp} regime={r.regime}"


class TestRegimeWithVolatility:
    def test_with_volatility(self) -> None:
        prices = [20, 40, 60, 80, 100, 120, 140]
        candles = [_c(p, idx=i) for i, p in enumerate(prices)]
        results = RegimeDetectionService.detect_with_volatility(candles, 3, 5)
        assert len(results) > 0
        regimes, _volatilities = zip(*results, strict=False)
        assert all(r.regime == Regime.TRENDING_BULLISH for r in regimes)

    def test_empty_with_volatility(self) -> None:
        assert RegimeDetectionService.detect_with_volatility([], 3, 5) == []
