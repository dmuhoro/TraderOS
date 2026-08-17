"""Sprint 38 (Slice C): durable candle store adapter for the Market Brain.

The Brain persists its bars (seeded history + tick aggregates) through the
existing provider candle store (``SQLiteHistoricalCandleRepository``), keyed by
``source="market_brain"`` + ``symbol=str(market_id)`` + the bar's own
timeframe. Upserts are idempotent by (source, symbol, timeframe, ts), so
re-seeding and replay never duplicate bars. ``load_candles`` reads across
timeframes in timestamp order to reconstruct the exact series the Brain's
index-based indicators read.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import Any

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.market_brain_service import CandleStorePort

_SOURCE = "market_brain"


class BrainCandleStore(CandleStorePort):
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def save_candles(self, market_id: uuid.UUID, candles: Iterable[Candle]) -> None:
        # The provider store's natural identity is one bar per (timeframe, ts).
        # Within one save the LAST bar at a timestamp wins — the exact rule the
        # store's upsert applies on conflict — so distinct bars sharing a
        # timestamp (e.g. a synthetic tape) collapse deterministically instead
        # of silently. Real exchange streams never collide, so the durable
        # projection is the honest restart-safe picture for them; the in-memory
        # index-based read keeps every bar regardless (Slice A).
        rows: dict[tuple[str, int], Candle] = {}
        for candle in candles:
            rows[(candle.timeframe.value, int(candle.timestamp.timestamp()))] = candle
        for candle in rows.values():
            self._repo.upsert(
                _SOURCE,
                str(market_id),
                candle.timeframe.value,
                [
                    {
                        "ts": int(candle.timestamp.timestamp()),
                        "open": float(candle.ohlcv.open),
                        "high": float(candle.ohlcv.high),
                        "low": float(candle.ohlcv.low),
                        "close": float(candle.ohlcv.close),
                        "volume": float(candle.ohlcv.volume),
                    }
                ],
            )

    def load_candles(self, market_id: uuid.UUID, limit: int) -> list[Candle]:
        rows = self._repo.load(_SOURCE, str(market_id), None, limit=limit)
        candles: list[Candle] = []
        for row in rows:
            candles.append(
                Candle(
                    market_id=market_id,
                    ohlcv=OHLCV(
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=Decimal(str(row["volume"])),
                    ),
                    timestamp=datetime.fromtimestamp(int(row["ts"]), tz=UTC),
                    timeframe=Timeframe(row["timeframe"]),
                )
            )
        return candles
