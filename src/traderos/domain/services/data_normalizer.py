from __future__ import annotations

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe

_INTERVAL_MAP: dict[str, Timeframe] = {
    "1m": Timeframe.MINUTE_1,
    "5m": Timeframe.MINUTE_5,
    "15m": Timeframe.MINUTE_15,
    "1h": Timeframe.HOUR_1,
    "4h": Timeframe.HOUR_4,
    "1d": Timeframe.DAY_1,
}


class DataNormalizer:
    @staticmethod
    def to_candle(
        ohlcv: CollectorOHLCV,
        market_id: str,
        interval: str = "1h",
        source: str = "",
    ) -> Candle:
        import uuid

        tf = _INTERVAL_MAP.get(interval, Timeframe.HOUR_1)
        return Candle(
            market_id=uuid.UUID(market_id),
            ohlcv=OHLCV(
                open=ohlcv.open,
                high=ohlcv.high,
                low=ohlcv.low,
                close=ohlcv.close,
                volume=ohlcv.volume,
            ),
            timestamp=ohlcv.timestamp,
            timeframe=tf,
            source=source or ohlcv.symbol,
        )

    @staticmethod
    def normalize(
        data: list[CollectorOHLCV],
    ) -> list[CollectorOHLCV]:
        return sorted(data, key=lambda x: x.timestamp)
