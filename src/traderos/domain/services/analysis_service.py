from __future__ import annotations

import math
from collections import deque
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
        closes: deque[float] = deque()
        total = 0.0
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            closes.append(close)
            total += close
            if len(closes) > window:
                total -= closes.popleft()
            if i < window - 1:
                continue
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candle.timestamp,
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
        closes: deque[float] = deque()
        rolling_total = 0.0
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            closes.append(close)
            rolling_total += close
            if len(closes) > window:
                rolling_total -= closes.popleft()
            if i < window - 1:
                continue
            if ema is None:
                ema = rolling_total / window
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
        gains_window: deque[float] = deque()
        losses_window: deque[float] = deque()
        gains = 0.0
        losses = 0.0
        previous_close: float | None = None
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            if previous_close is None:
                previous_close = close
                continue
            change = close - previous_close
            previous_close = close
            gain = change if change > 0 else 0.0
            loss = -change if change < 0 else 0.0
            gains_window.append(gain)
            losses_window.append(loss)
            gains += gain
            losses += loss
            if len(gains_window) > window:
                gains -= gains_window.popleft()
                losses -= losses_window.popleft()
            if i < window:
                continue
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
                    timestamp=candle.timestamp,
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
        tr_window: deque[float] = deque()
        tr_total = 0.0
        previous_close: float | None = None
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            if previous_close is None:
                previous_close = close
                continue
            high = float(candle.ohlcv.high)
            low = float(candle.ohlcv.low)
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
            previous_close = close
            tr_window.append(tr)
            tr_total += tr
            if len(tr_window) > window:
                tr_total -= tr_window.popleft()
            if i < window:
                continue
            atr = tr_total / window
            result.append(
                Indicator(
                    market_id=market_id,
                    timestamp=candle.timestamp,
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
        closes: deque[float] = deque()
        origin = float(candles[0].ohlcv.close)
        offset_total = 0.0
        offset_squares = 0.0
        for i, candle in enumerate(candles):
            close = float(candle.ohlcv.close)
            if len(closes) == window:
                expired = closes.popleft() - origin
                offset_total -= expired
                offset_squares -= expired * expired
            closes.append(close)
            offset = close - origin
            offset_total += offset
            offset_squares += offset * offset
            if i < window - 1:
                continue
            mean_offset = offset_total / window
            mean = origin + mean_offset
            variance = max(0.0, offset_squares / window - mean_offset * mean_offset)
            std = math.sqrt(variance)
            ts = candle.timestamp
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
        highs: deque[tuple[int, float]] = deque()
        lows: deque[tuple[int, float]] = deque()
        for i in range(len(candles)):
            high_value = float(candles[i].ohlcv.high)
            low_value = float(candles[i].ohlcv.low)
            while highs and highs[-1][1] <= high_value:
                highs.pop()
            highs.append((i, high_value))
            while lows and lows[-1][1] >= low_value:
                lows.pop()
            lows.append((i, low_value))
            expired = i - k_window
            while highs and highs[0][0] <= expired:
                highs.popleft()
            while lows and lows[0][0] <= expired:
                lows.popleft()
            if i < k_window - 1:
                continue
            high = highs[0][1]
            low = lows[0][1]
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
        d_window_values: deque[float] = deque()
        d_total = 0.0
        for i, k_value in enumerate(k_values):
            d_window_values.append(k_value)
            d_total += k_value
            if len(d_window_values) > d_window:
                d_total -= d_window_values.popleft()
            if i < d_window - 1:
                continue
            d_val = d_total / d_window
            d_indicators.append(
                Indicator(
                    market_id=market_id,
                    timestamp=k_timestamps[i],
                    name=f"stoch_d_{k_window}_{d_window}",
                    value=d_val,
                )
            )
        return Stochastic(k=k_indicators, d=d_indicators)
