from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any

from traderos.domain.collectors.base import DataCollector
from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe

# Seconds per timeframe bucket, for cache-window sizing and staleness checks.
_SPACING: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 21600,
    "1d": 86400,
}

_TIMEFRAME_BY_VALUE: dict[str, Timeframe] = {t.value: t for t in Timeframe}


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item[key]
    return getattr(item, key)


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(int(value), tz=UTC)


def _as_timestamp(item: Any) -> datetime:
    if isinstance(item, dict):
        value = item.get("timestamp", item.get("ts"))
    else:
        value = getattr(item, "timestamp", getattr(item, "ts", None))
    return _to_dt(value)


@dataclass
class HistoricalDataService:
    """Provider-neutral historical candles with a durable, reusable store.

    Sources are registered as :class:`DataCollector`; candles are normalised
    to the domain :class:`Candle` and (when a cache repo is injected) persisted
    by ``(source, symbol, timeframe, ts)`` so a trusted bar is reused instead of
    refetched — grounding future automated trading on stored history.
    """

    collectors: dict[str, DataCollector] = field(default_factory=dict)
    cache: Any | None = None

    def register(self, collector: DataCollector) -> None:
        self.collectors[collector.collector_type.value] = collector

    def available_sources(self) -> list[str]:
        return list(self.collectors)

    @staticmethod
    def market_id(source: str, symbol: str) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"traderos://{source}/{symbol}")

    def fetch(
        self,
        source: str,
        timeframe: str,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        collector = self.collectors.get(source)
        if collector is None:
            raise ValueError(f"unknown data source: {source!r}")
        rows = collector.fetch_historical(symbol, timeframe, start, end, limit)
        return self._to_candles(source, timeframe, symbol, rows)

    def _to_candles(
        self, source: str, timeframe: str, symbol: str, items: list[Any]
    ) -> list[Candle]:
        tf = _TIMEFRAME_BY_VALUE.get(timeframe, Timeframe.HOUR_1)
        mid = self.market_id(source, symbol)
        return [
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(str(_get(it, "open"))),
                    high=Decimal(str(_get(it, "high"))),
                    low=Decimal(str(_get(it, "low"))),
                    close=Decimal(str(_get(it, "close"))),
                    volume=Decimal(str(_get(it, "volume"))),
                ),
                timestamp=_as_timestamp(it),
                timeframe=tf,
                source=source,
            )
            for it in items
        ]

    @staticmethod
    def _store(candle: Candle) -> dict[str, Any]:
        return {
            "ts": int(candle.timestamp.timestamp()),
            "open": float(candle.ohlcv.open),
            "high": float(candle.ohlcv.high),
            "low": float(candle.ohlcv.low),
            "close": float(candle.ohlcv.close),
            "volume": float(candle.ohlcv.volume),
        }

    def get_candles(
        self,
        source: str,
        timeframe: str,
        symbol: str,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
        use_cache: bool = True,
    ) -> list[Candle]:
        spacing = _SPACING.get(timeframe, 3600)
        now = datetime.now(tz=UTC)
        end = end or now
        start = start or (end - timedelta(seconds=spacing * max(1, limit)))
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        expected = min(limit, ((end_ts - start_ts) // spacing) + 1)

        if use_cache and self.cache is not None:
            cached = self.cache.load(source, symbol, timeframe, start_ts, end_ts, None)
            if len(cached) >= max(1, int(expected * 0.8)):
                return self._to_candles(source, timeframe, symbol, cached)

        candles = self.fetch(source, timeframe, symbol, start, end, limit)
        if self.cache is not None:
            self.cache.upsert(source, symbol, timeframe, [self._store(c) for c in candles])
        return candles
