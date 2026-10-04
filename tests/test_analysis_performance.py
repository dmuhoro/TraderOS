from __future__ import annotations

import hashlib
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

MARKET_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _candle(index: int, close: int) -> Candle:
    return Candle(
        market_id=MARKET_ID,
        ohlcv=OHLCV(
            open=Decimal(close),
            high=Decimal(close + 2),
            low=Decimal(close - 2),
            close=Decimal(close),
            volume=Decimal(1000),
        ),
        timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        timeframe=Timeframe.MINUTE_1,
    )


def test_rolling_indicator_golden_is_bit_for_bit_unchanged() -> None:
    closes = [25, 75] * 4
    candles = [
        Candle(
            MARKET_ID,
            OHLCV(Decimal(value), Decimal(100), Decimal(0), Decimal(value), Decimal(1000)),
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
            Timeframe.MINUTE_1,
        )
        for i, value in enumerate(closes)
    ]
    outputs: list[float] = []
    for name, args in (
        ("compute_sma", (candles, 2)),
        ("compute_ema", (candles, 2)),
        ("compute_rsi", (candles, 2)),
        ("compute_atr", (candles, 2)),
        ("compute_bollinger_bands", (candles, 2)),
        ("compute_stochastics", (candles, 2, 2)),
    ):
        result = getattr(AnalysisService, name)(*args)
        if name in {"compute_bollinger_bands", "compute_stochastics"}:
            outputs.extend(item.value for series in result for item in series)
        else:
            outputs.extend(item.value for item in result)
    digest = hashlib.sha256("|".join(value.hex() for value in outputs).encode()).hexdigest()
    assert digest == "5b0d04246eec95d1b50afbdc89464399ec057f843bbad14c8df0af3ebc27f64d"


def test_all_indicators_on_5000_candles_fit_one_second_budget() -> None:
    candles = [_candle(i, 100 + (i * 17) % 101) for i in range(5000)]
    # Warm interpreter/import paths outside the measured call, equally for each
    # implementation; the budget applies to one complete indicator batch.
    AnalysisService.compute_sma(candles, 20)
    AnalysisService.compute_ema(candles, 20)
    AnalysisService.compute_rsi(candles, 14)
    AnalysisService.compute_atr(candles, 14)
    AnalysisService.compute_bollinger_bands(candles, 20)
    AnalysisService.compute_stochastics(candles, 14, 3)
    started = time.perf_counter()
    AnalysisService.compute_sma(candles, 20)
    AnalysisService.compute_ema(candles, 20)
    AnalysisService.compute_rsi(candles, 14)
    AnalysisService.compute_atr(candles, 14)
    AnalysisService.compute_bollinger_bands(candles, 20)
    AnalysisService.compute_stochastics(candles, 14, 3)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"5000-candle indicator batch took {elapsed:.3f}s (budget 1.0s)"
