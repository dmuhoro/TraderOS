"""Frozen, deterministic dataset for the backtest oracle (G-06).

This is the single source of truth a conformance test locks the engine to. It
is intentionally NOT derived from live feeds: it is a fixed-seed pseudo-random
walk so the exact candles are reproducible on any machine forever. If the
engine's fill/cost semantics ever change, the committed reference PnL in
``test_backtest_oracle.py`` changes, and the oracle test fails — signalling a
regression before it can silently change every backtest result.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe

MARKET_ID = uuid.UUID("8b2b6f3c-2d3a-4e9a-9f0a-1c2d3e4f5a6b")
SEED = 20260804
COUNT = 120


def oracle_candles(count: int = COUNT) -> list[Candle]:
    rng = random.Random(SEED)
    price = 100.0
    start = datetime(2025, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    for i in range(count):
        ret = rng.gauss(0.0005, 0.015)
        open_price = price
        close_price = max(1.0, price * (1 + ret))
        high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0.0, 0.004)))
        low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0.0, 0.004)))
        volume = Decimal(str(round(rng.uniform(500.0, 3000.0), 2)))
        candles.append(
            Candle(
                market_id=MARKET_ID,
                ohlcv=OHLCV(
                    open=Decimal(str(round(open_price, 4))),
                    high=Decimal(str(round(high_price, 4))),
                    low=Decimal(str(round(low_price, 4))),
                    close=Decimal(str(round(close_price, 4))),
                    volume=volume,
                ),
                timestamp=start + timedelta(hours=i),
                timeframe=Timeframe.HOUR_1,
            )
        )
        price = close_price
    return candles
