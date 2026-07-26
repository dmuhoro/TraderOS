from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Candle


class SweepEvent(NamedTuple):
    timestamp: datetime
    event_type: str
    description: str


class SweepDetectionService:
    @staticmethod
    def detect_sweeps(
        candles: list[Candle],
        swing_highs: list[float],
        swing_lows: list[float],
    ) -> list[SweepEvent]:
        sweeps: list[SweepEvent] = []
        if not candles:
            return sweeps

        last_high = swing_highs[-1] if swing_highs else None
        last_low = swing_lows[-1] if swing_lows else None

        if last_high is None and last_low is None:
            return sweeps

        for candle in candles:
            close = float(candle.ohlcv.close)
            high = float(candle.ohlcv.high)
            low = float(candle.ohlcv.low)

            if last_low is not None and low < last_low and close > last_low:
                sweeps.append(
                    SweepEvent(
                        timestamp=candle.timestamp,
                        event_type="Liquidity Sweep (Bullish)",
                        description=f"Price swept below previous low {last_low:.4f} and rejected.",
                    )
                )

            if last_high is not None and high > last_high and close < last_high:
                sweeps.append(
                    SweepEvent(
                        timestamp=candle.timestamp,
                        event_type="Liquidity Sweep (Bearish)",
                        description=f"Price swept above previous high {last_high:.4f} and rejected.",
                    )
                )

        return sweeps
