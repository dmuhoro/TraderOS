from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector


class MockDataCollector(DataCollector):
    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.MOCK

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        now = datetime.now(tz=UTC)
        result: list[CollectorOHLCV] = []
        for i in range(limit):
            ts = now - timedelta(hours=i * 4)
            price = Decimal(50000 + i * 10 + (i % 5) * 100)
            result.append(
                CollectorOHLCV(
                    open=price,
                    high=price + Decimal(200),
                    low=price - Decimal(200),
                    close=price + Decimal(50),
                    volume=Decimal(1000),
                    timestamp=ts,
                    symbol=symbol,
                )
            )
        return result

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol) and len(symbol) > 2
