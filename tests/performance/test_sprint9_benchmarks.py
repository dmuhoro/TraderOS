import time
from datetime import UTC
from datetime import datetime

from traderos.infrastructure.market_stream import StreamingMarketDataService


class Transport:
    def close(self):
        pass


def test_tick_ingestion_throughput_benchmark():
    service = StreamingMarketDataService(Transport(), max_queue=20_000)
    started = time.perf_counter()
    for index in range(10_000):
        service.ingest(
            {
                "symbol": "BTCUSDT",
                "price": str(100 + index / 1000),
                "quantity": "1",
                "timestamp": datetime.now(tz=UTC).timestamp(),
            }
        )
    elapsed = time.perf_counter() - started
    assert service.dropped_ticks == 0
    assert elapsed < 2.0
