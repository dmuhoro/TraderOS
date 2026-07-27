from __future__ import annotations

from datetime import datetime

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector


class YFinanceCollector(DataCollector):
    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.YFINANCE

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        return []

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol)
