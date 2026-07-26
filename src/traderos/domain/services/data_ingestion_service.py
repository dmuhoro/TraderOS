from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import NamedTuple

from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType


class DataSource(NamedTuple):
    market_id: uuid.UUID
    symbol: str
    collector_type: CollectorType
    timeframe: str


@dataclass
class DataIngestionService:
    registry: CollectorRegistry
    sources: list[DataSource] = field(default_factory=list)

    def add_source(
        self,
        market_id: uuid.UUID,
        symbol: str,
        collector_type: CollectorType = CollectorType.MOCK,
        timeframe: str = "1d",
    ) -> DataSource:
        source = DataSource(market_id, symbol, collector_type, timeframe)
        self.sources.append(source)
        return source

    def fetch_latest(
        self,
        source: DataSource,
        limit: int = 100,
    ) -> list[dict]:
        collector = self.registry.get(source.collector_type)
        if collector is None:
            raise ValueError(f"No collector for {source.collector_type}")
        raw = collector.fetch_historical(source.symbol, source.timeframe, limit=limit)
        result: list[dict] = []
        for r in raw:
            ts = r.timestamp
            if hasattr(r, "timestamp") and isinstance(ts, datetime):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)
            result.append(
                {
                    "timestamp": ts_str,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
            )
        return result

    def fetch_all(self, limit: int = 100) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for source in self.sources:
            result[source.symbol] = self.fetch_latest(source, limit)
        return result

    def get_latest_close(self, market_id: uuid.UUID) -> float | None:
        source = next((s for s in self.sources if s.market_id == market_id), None)
        if source is None:
            return None
        data = self.fetch_latest(source, limit=1)
        if data:
            return float(data[0]["close"])
        return None

    def remove_source(self, symbol: str) -> None:
        self.sources = [s for s in self.sources if s.symbol != symbol]
