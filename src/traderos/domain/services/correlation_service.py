from __future__ import annotations

import math
from typing import NamedTuple

from traderos.domain.entities import Candle


class CorrelationResult(NamedTuple):
    value: float
    n_periods: int


class CorrelationService:
    @staticmethod
    def compute_correlation(
        candles_a: list[Candle],
        candles_b: list[Candle],
    ) -> CorrelationResult | None:
        if not candles_a or not candles_b:
            return None

        a_by_ts = {c.timestamp: float(c.ohlcv.close) for c in candles_a}
        b_by_ts = {c.timestamp: float(c.ohlcv.close) for c in candles_b}

        common_ts = sorted(ts for ts in a_by_ts if ts in b_by_ts)
        if len(common_ts) < 2:
            return None

        a_prices = [a_by_ts[ts] for ts in common_ts]
        b_prices = [b_by_ts[ts] for ts in common_ts]

        a_returns = [
            (a_prices[i] - a_prices[i - 1]) / a_prices[i - 1] for i in range(1, len(a_prices))
        ]
        b_returns = [
            (b_prices[i] - b_prices[i - 1]) / b_prices[i - 1] for i in range(1, len(b_prices))
        ]

        n = len(a_returns)
        if n < 2:
            return None

        mean_a = sum(a_returns) / n
        mean_b = sum(b_returns) / n

        cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(a_returns, b_returns, strict=True))
        var_a = sum((a - mean_a) ** 2 for a in a_returns)
        var_b = sum((b - mean_b) ** 2 for b in b_returns)

        if var_a == 0 or var_b == 0:
            return None

        r = cov / math.sqrt(var_a * var_b)
        r = max(-1.0, min(1.0, r))

        return CorrelationResult(value=r, n_periods=n)

    @staticmethod
    def compute_correlation_matrix(
        series: dict[str, list[Candle]],
    ) -> dict[tuple[str, str], float]:
        result: dict[tuple[str, str], float] = {}
        symbols = list(series.keys())
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corr = CorrelationService.compute_correlation(
                    series[symbols[i]],
                    series[symbols[j]],
                )
                if corr is not None:
                    result[(symbols[i], symbols[j])] = corr.value
        return result
