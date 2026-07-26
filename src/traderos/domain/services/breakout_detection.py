from __future__ import annotations

import math
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Candle


class BreakoutEvent(NamedTuple):
    timestamp: datetime
    event_type: str
    description: str


class BreakoutDetectionService:
    @staticmethod
    def _std(values: list[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        return math.sqrt(variance)

    @staticmethod
    def analyze(
        candles: list[Candle],
        vol_threshold: float = 0.001,
        sensitivity: float = 2.0,
        vol_window: int = 10,
        ma_window: int = 20,
    ) -> list[BreakoutEvent]:
        if not candles or len(candles) < ma_window + vol_window:
            return []

        returns: list[float] = []
        for i in range(1, len(candles)):
            prev = float(candles[i - 1].ohlcv.close)
            curr = float(candles[i].ohlcv.close)
            returns.append((curr - prev) / prev if prev != 0 else 0.0)

        events: list[BreakoutEvent] = []
        prev_consolidating = False

        for i in range(ma_window + vol_window - 2, len(returns)):
            vol_std = BreakoutDetectionService._std(
                returns[i - vol_window + 1 : i + 1],
            )

            vol_stds_window = [
                BreakoutDetectionService._std(returns[j - vol_window + 1 : j + 1])
                for j in range(i - ma_window + 1, i + 1)
            ]
            vol_ma = sum(vol_stds_window) / len(vol_stds_window) if vol_stds_window else 0.0

            is_consolidating = vol_std < vol_threshold
            is_breaking_out = (vol_std > vol_ma * sensitivity) and not prev_consolidating
            candle_idx = i + 1
            ts = candles[candle_idx].timestamp

            if is_breaking_out:
                close_val = float(candles[candle_idx].ohlcv.close)
                events.append(
                    BreakoutEvent(
                        timestamp=ts,
                        event_type="Breakout",
                        description=f"Volatility breakout detected at {close_val:.4f}",
                    )
                )
            elif is_consolidating and not prev_consolidating:
                events.append(
                    BreakoutEvent(
                        timestamp=ts,
                        event_type="Consolidation",
                        description="Market entering consolidation phase.",
                    )
                )

            prev_consolidating = is_consolidating

        return events
