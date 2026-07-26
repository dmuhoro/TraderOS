from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.domain.services.data_ingestion_service import DataIngestionService


class _MockCollector(DataCollector):
    collector_type = CollectorType.MOCK

    def fetch_historical(self, symbol, interval="1d", start=None, end=None, limit=500):
        return [
            CollectorOHLCV(
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
                timestamp=datetime.now(tz=UTC),
                symbol=symbol,
            )
            for _ in range(limit)
        ]

    def validate_symbol(self, symbol):
        return True


class TestDataIngestionService:
    def test_add_source(self) -> None:
        registry = CollectorRegistry()
        svc = DataIngestionService(registry=registry)
        mid = uuid.uuid4()
        source = svc.add_source(mid, "BTC/USD", CollectorType.MOCK)
        assert source.symbol == "BTC/USD"
        assert source.collector_type == CollectorType.MOCK
        assert len(svc.sources) == 1

    def test_fetch_latest_mock(self) -> None:
        registry = CollectorRegistry()
        registry.register(_MockCollector())
        svc = DataIngestionService(registry=registry)
        mid = uuid.uuid4()
        source = svc.add_source(mid, "BTC/USD", CollectorType.MOCK)
        data = svc.fetch_latest(source, limit=5)
        assert len(data) == 5
        assert "close" in data[0]

    def test_fetch_all(self) -> None:
        registry = CollectorRegistry()
        registry.register(_MockCollector())
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTC/USD", CollectorType.MOCK)
        svc.add_source(uuid.uuid4(), "ETH/USD", CollectorType.MOCK)
        result = svc.fetch_all(limit=3)
        assert "BTC/USD" in result
        assert "ETH/USD" in result

    def test_remove_source(self) -> None:
        registry = CollectorRegistry()
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTC/USD")
        svc.add_source(uuid.uuid4(), "ETH/USD")
        svc.remove_source("BTC/USD")
        assert len(svc.sources) == 1
        assert svc.sources[0].symbol == "ETH/USD"

    def test_fetch_nonexistent_collector(self) -> None:
        registry = CollectorRegistry()
        svc = DataIngestionService(registry=registry)
        source = svc.add_source(uuid.uuid4(), "TEST", CollectorType.BINANCE)
        try:
            svc.fetch_latest(source)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass
