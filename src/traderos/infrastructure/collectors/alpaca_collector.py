from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector


def _frame_interval(interval: str):
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.timeframe import TimeFrameUnit

    mapping = {
        "1m": TimeFrame.Minute,
        "5m": TimeFrame(5, TimeFrameUnit.Minute),  # type: ignore[arg-type]
        "15m": TimeFrame(15, TimeFrameUnit.Minute),  # type: ignore[arg-type]
        "1h": TimeFrame.Hour,
        "4h": TimeFrame(6, TimeFrameUnit.Hour),  # type: ignore[arg-type]
        "1d": TimeFrame.Day,
    }
    return mapping.get(interval, TimeFrame.Hour)  # type: ignore[no-any-return]


class AlpacaCollector(DataCollector):
    """Live historical candles from Alpaca via the provider-neutral contract.

    Uses the crypto historical feed (e.g. ``BTC/USD``) so any key grants read
    access; the same credentials live in the environment as the trading client.
    The data client is constructed lazily and the response is normalised to
    :class:`CollectorOHLCV`, so BarSet structure changes cannot leak upstream.
    """

    def __init__(self, api_key: str | None = None, secret_key: str | None = None) -> None:
        self._api_key = api_key
        self._secret_key = secret_key

    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.ALPACA

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol) and "/" in symbol and len(symbol) >= 5

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        from alpaca.data.historical.crypto import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest

        client = CryptoHistoricalDataClient(
            self._api_key or os.getenv("ALPACA_API_KEY"),
            self._secret_key or os.getenv("ALPACA_SECRET_KEY"),
        )
        request_one = CryptoBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=_frame_interval(interval),  # type: ignore[arg-type]
            start=start,
            end=end,
            limit=limit,
        )
        bars = client.get_crypto_bars(request_one)
        df = getattr(bars, "df", None)
        result: list[CollectorOHLCV] = []
        if df is None:
            return result
        for index, row in df.iterrows():
            sym = index[0] if isinstance(index, tuple) else index
            ts = index[1] if isinstance(index, tuple) else index
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if not isinstance(ts, datetime):
                ts = datetime.fromisoformat(str(ts))
            result.append(
                CollectorOHLCV(
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])),
                    timestamp=ts,
                    symbol=sym,
                )
            )
        return result
