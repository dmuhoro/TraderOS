from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import NamedTuple

from traderos.domain.entities import Candle
from traderos.domain.services.analysis_service import AnalysisService


class Regime(StrEnum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


class RegimeResult(NamedTuple):
    timestamp: datetime
    regime: Regime


class RegimeDetectionService:
    @staticmethod
    def detect(
        candles: list[Candle],
        fast_window: int = 50,
        slow_window: int = 200,
    ) -> list[RegimeResult]:
        if not candles or len(candles) < slow_window:
            return []

        fast_sma = AnalysisService.compute_sma(candles, fast_window)
        slow_sma = AnalysisService.compute_sma(candles, slow_window)

        fast_by_ts = {i.timestamp: i.value for i in fast_sma}
        slow_by_ts = {i.timestamp: i.value for i in slow_sma}
        candle_by_ts = {c.timestamp: c for c in candles}

        common_ts = sorted(ts for ts in fast_by_ts if ts in slow_by_ts and ts in candle_by_ts)

        results: list[RegimeResult] = []
        for ts in common_ts:
            candle = candle_by_ts[ts]
            close = float(candle.ohlcv.close)
            fast_val = fast_by_ts[ts]
            slow_val = slow_by_ts[ts]

            if close > fast_val > slow_val:
                regime = Regime.TRENDING_BULLISH
            elif close < fast_val < slow_val:
                regime = Regime.TRENDING_BEARISH
            else:
                regime = Regime.RANGING

            results.append(RegimeResult(timestamp=ts, regime=regime))

        return results

    @staticmethod
    def detect_with_volatility(
        candles: list[Candle],
        fast_window: int = 50,
        slow_window: int = 200,
        vol_window: int = 14,
        vol_multiplier: float = 1.5,
    ) -> list[tuple[RegimeResult, bool]]:
        regime_results = RegimeDetectionService.detect(candles, fast_window, slow_window)
        if not regime_results:
            return []

        atr_values = AnalysisService.compute_atr(candles, vol_window)
        if not atr_values:
            return [(r, False) for r in regime_results]

        atr_mean = sum(a.value for a in atr_values) / len(atr_values)
        atr_by_ts = {a.timestamp: a.value for a in atr_values}

        return [
            (r, atr_by_ts.get(r.timestamp, 0) > atr_mean * vol_multiplier)
            for r in regime_results
            if r.timestamp in atr_by_ts
        ]
