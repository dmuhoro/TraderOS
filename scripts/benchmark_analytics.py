from __future__ import annotations

import json
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.analysis_service import AnalysisService


def main() -> None:
    market_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    candles = [
        Candle(
            market_id,
            OHLCV(
                Decimal(close),
                Decimal(close + 2),
                Decimal(close - 2),
                Decimal(close),
                Decimal(1000),
            ),
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            Timeframe.MINUTE_1,
        )
        for index in range(5000)
        for close in (100 + (index * 17) % 101,)
    ]
    calculations = (
        lambda: AnalysisService.compute_sma(candles, 20),
        lambda: AnalysisService.compute_ema(candles, 20),
        lambda: AnalysisService.compute_rsi(candles, 14),
        lambda: AnalysisService.compute_atr(candles, 14),
        lambda: AnalysisService.compute_bollinger_bands(candles, 20),
        lambda: AnalysisService.compute_stochastics(candles, 14, 3),
    )
    for calculate in calculations:
        calculate()
    started = time.perf_counter()
    for calculate in calculations:
        calculate()
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "candles": len(candles),
                "indicator_batch_seconds": round(elapsed, 6),
                "indicators": [
                    "compute_sma",
                    "compute_ema",
                    "compute_rsi",
                    "compute_atr",
                    "compute_bollinger_bands",
                    "compute_stochastics",
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
