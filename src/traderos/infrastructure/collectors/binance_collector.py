from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector


class BinanceCollector(DataCollector):
    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.BINANCE

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        import json
        from urllib.error import URLError
        from urllib.request import urlopen

        params = f"symbol={symbol}&interval={interval}&limit={limit}"
        url = f"https://api.binance.com/api/v3/klines?{params}"
        try:
            with urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except URLError:
            return []

        result: list[CollectorOHLCV] = []
        for entry in data:
            result.append(
                CollectorOHLCV(
                    open=Decimal(str(entry[1])),
                    high=Decimal(str(entry[2])),
                    low=Decimal(str(entry[3])),
                    close=Decimal(str(entry[4])),
                    volume=Decimal(str(entry[5])),
                    timestamp=datetime.fromtimestamp(entry[0] / 1000, tz=UTC),
                    symbol=symbol,
                )
            )
        return result

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol) and len(symbol) > 2
