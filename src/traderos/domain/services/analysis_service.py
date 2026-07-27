from __future__ import annotations

import math
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Candle
from traderos.domain.entities import Indicator


class BollingerBands(NamedTuple):
    middle: list[Indicator]
    upper: list[Indicator]
    lower: list[Indicator]


class Stochastic(NamedTuple):
    k: list[Indicator]
    d: list[Indicator]


class AnalysisService:
    @staticmethod
    def compute_sma(candles: list[Candle], window: int) -> list[Indicator]:
        if not candles or window < 1:
            return []
        market_id = candles[0].market_id
        name = f"sma_{window}"
        result: list[Indicator] = []
        for i in range(len(candles)):
            if i < window - 1:
                continue
            total = sum(float(candles[j].ohlcv.close) for j in range(i - window + 1, i + 1))
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candles[i].timestamp,
                    name=name,
                    value=total / window,
                )
            )
        return result

    @staticmethod
    def compute_ema(candles: list[Candle], window: int) -> list[Indicator]:
        if not candles or window < 1:
            return []
        market_id = candles[0].market_id
        name = f"ema_{window}"
        multiplier = 2.0 / (window + 1)
        result: list[Indicator] = []
        ema: float | None = None
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            if i < window - 1:
                continue
            if ema is None:
                total = sum(float(candles[j].ohlcv.close) for j in range(i - window + 1, i + 1))
                ema = total / window
            else:
                ema = (close - ema) * multiplier + ema
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candle.timestamp,
                    name=name,
                    value=ema,
                )
            )
        return result

    @staticmethod
    def compute_rsi(candles: list[Candle], window: int = 14) -> list[Indicator]:
        if not candles or window < 1:
            return []
        market_id = candles[0].market_id
        name = f"rsi_{window}"
        result: list[Indicator] = []
        for i in range(len(candles)):
            if i < window:
                continue
            gains = 0.0
            losses = 0.0
            for j in range(i - window + 1, i + 1):
                change = float(candles[j].ohlcv.close) - float(candles[j - 1].ohlcv.close)
                if change > 0:
                    gains += change
                else:
                    losses -= change
            avg_gain = gains / window
            avg_loss = losses / window
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candles[i].timestamp,
                    name=name,
                    value=rsi,
                )
            )
        return result

    @staticmethod
    def compute_atr(candles: list[Candle], window: int = 14) -> list[Indicator]:
        if not candles or window < 1:
            return []
        market_id = candles[0].market_id
        name = f"atr_{window}"
        result: list[Indicator] = []
        for i in range(len(candles)):
            if i < window:
                continue
            tr_values: list[float] = []
            for j in range(i - window + 1, i + 1):
                high = float(candles[j].ohlcv.high)
                low = float(candles[j].ohlcv.low)
                prev_close = float(candles[j - 1].ohlcv.close)
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            atr = sum(tr_values) / window
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candles[i].timestamp,
                    name=name,
                    value=atr,
                )
            )
        return result

    @staticmethod
    def compute_bollinger_bands(
        candles: list[Candle],
        window: int = 20,
        num_std: float = 2.0,
    ) -> BollingerBands:
        if not candles or window < 1:
            return BollingerBands([], [], [])
        market_id = candles[0].market_id
        middle_list: list[Indicator] = []
        upper_list: list[Indicator] = []
        lower_list: list[Indicator] = []
        for i in range(len(candles)):
            if i < window - 1:
                continue
            closes = [float(candles[j].ohlcv.close) for j in range(i - window + 1, i + 1)]
            mean = sum(closes) / window
            variance = sum((c - mean) ** 2 for c in closes) / window
            std = math.sqrt(variance)
            ts = candles[i].timestamp
            middle_list.append(
                Indicator(
                    market_id=market_id,
                    timestamp=ts,
                    name=f"bb_middle_{window}",
                    value=mean,
                )
            )
            upper_list.append(
                Indicator(
                    market_id=market_id,
                    timestamp=ts,
                    name=f"bb_upper_{window}",
                    value=mean + num_std * std,
                )
            )
            lower_list.append(
                Indicator(
                    market_id=market_id,
                    timestamp=ts,
                    name=f"bb_lower_{window}",
                    value=mean - num_std * std,
                )
            )
        return BollingerBands(middle=middle_list, upper=upper_list, lower=lower_list)

    @staticmethod
    def compute_stochastics(
        candles: list[Candle],
        k_window: int = 14,
        d_window: int = 3,
    ) -> Stochastic:
        if not candles or k_window < 1 or d_window < 1:
            return Stochastic([], [])
        market_id = candles[0].market_id
        k_values: list[float] = []
        k_timestamps: list[datetime] = []
        for i in range(len(candles)):
            if i < k_window - 1:
                continue
            high = max(float(candles[j].ohlcv.high) for j in range(i - k_window + 1, i + 1))
            low = min(float(candles[j].ohlcv.low) for j in range(i - k_window + 1, i + 1))
            close = float(candles[i].ohlcv.close)
            if high == low:
                k = 50.0
            else:
                k = (close - low) / (high - low) * 100.0
            k_values.append(k)
            k_timestamps.append(candles[i].timestamp)
        k_indicators = [
            Indicator(market_id=market_id, timestamp=ts, name=f"stoch_k_{k_window}", value=v)
            for ts, v in zip(k_timestamps, k_values, strict=True)
        ]
        d_indicators: list[Indicator] = []
        for i in range(len(k_values)):
            if i < d_window - 1:
                continue
            d_val = sum(k_values[i - d_window + 1 : i + 1]) / d_window
            d_indicators.append(
                Indicator(
                    market_id=market_id,
                    timestamp=k_timestamps[i],
                    name=f"stoch_d_{k_window}_{d_window}",
                    value=d_val,
                )
            )
        return Stochastic(k=k_indicators, d=d_indicators)
