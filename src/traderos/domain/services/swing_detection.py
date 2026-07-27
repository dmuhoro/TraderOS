from __future__ import annotations

from typing import NamedTuple

from traderos.domain.entities import Candle
from traderos.domain.entities import Indicator


class SwingResult(NamedTuple):
    highs: list[Indicator]
    lows: list[Indicator]


class SwingDetectionService:
    @staticmethod
    def detect_swings(candles: list[Candle], window: int = 5) -> SwingResult:
        if not candles or window < 1:
            return SwingResult([], [])
        market_id = candles[0].market_id
        highs: list[Indicator] = []
        lows: list[Indicator] = []
        n = len(candles)
        for i in range(window, n - window):
            high = float(candles[i].ohlcv.high)
            low = float(candles[i].ohlcv.low)
            if all(
                high > float(candles[j].ohlcv.high)
                for j in range(i - window, i + window + 1)
                if j != i
            ):
                highs.append(
                    Indicator(
                        market_id=market_id,
                        timestamp=candles[i].timestamp,
                        name="swing_high",
                        value=high,
                    )
                )
            if all(
                low < float(candles[j].ohlcv.low)
                for j in range(i - window, i + window + 1)
                if j != i
            ):
                lows.append(
                    Indicator(
                        market_id=market_id,
                        timestamp=candles[i].timestamp,
                        name="swing_low",
                        value=low,
                    )
                )
        return SwingResult(highs=highs, lows=lows)

    @staticmethod
    def get_recent_swings(
        candles: list[Candle],
        window: int = 5,
        count: int = 5,
    ) -> SwingResult:
        swings = SwingDetectionService.detect_swings(candles, window)
        return SwingResult(
            highs=swings.highs[-count:] if swings.highs else [],
            lows=swings.lows[-count:] if swings.lows else [],
        )
