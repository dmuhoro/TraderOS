from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.domain.services.historical_data import HistoricalDataService
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.repositories.sqlite.historical_candles import (
    SQLiteHistoricalCandleRepository,
)


class FakeCollector(DataCollector):
    def __init__(self, rows: list[CollectorOHLCV]) -> None:
        self._rows = rows

    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.MOCK

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol)

    def fetch_historical(self, symbol, interval, start=None, end=None, limit=500):
        return self._rows[:limit]


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn, target_version=7)
    conn.commit()
    return conn


def _rows(n: int = 5, base: int = 64000) -> list[CollectorOHLCV]:
    out = []
    for i in range(n):
        out.append(
            CollectorOHLCV(
                open=Decimal(base + i),
                high=Decimal(base + i + 2),
                low=Decimal(base + i - 1),
                close=Decimal(base + i),
                volume=Decimal(100 + i),
                timestamp=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=i),
                symbol="BTC/USD",
            )
        )
    return out


def test_get_candles_fetch_then_cached_recall_is_identical():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    source_rows = _rows()
    service = HistoricalDataService(
        collectors={"mock": FakeCollector(source_rows)},
        cache=repo,
    )

    first = service.get_candles("mock", "1h", "BTC/USD", limit=5)
    assert len(first) == 5
    assert repo.count("mock", "BTC/USD", "1h") == 5

    second = service.get_candles("mock", "1h", "BTC/USD", limit=5)
    assert [c.timestamp for c in first] == [c.timestamp for c in second]
    assert [float(c.ohlcv.close) for c in first] == [float(c.ohlcv.close) for c in second]


def test_repo_upsert_idempotent_and_load_count():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    ts0 = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
    rows = [
        {"ts": ts0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"ts": ts0 + 3600, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 20},
    ]
    repo.upsert("binance", "BTCUSDT", "1h", rows)
    repo.upsert("binance", "BTCUSDT", "1h", [rows[1]])  # idempotent conflict
    assert repo.count("binance", "BTCUSDT", "1h") == 2
    assert repo.last_ts("binance", "BTCUSDT", "1h") == ts0 + 3600
    loaded = repo.load("binance", "BTCUSDT", "1h")
    assert len(loaded) == 2
    assert loaded[0]["ts"] == ts0
    assert "timestamp" not in loaded[0]  # store uses "ts" key


def test_get_candles_builds_market_id_and_normalizes():
    service = HistoricalDataService(collectors={"mock": FakeCollector(_rows(3))})
    candles = service.get_candles("mock", "1h", "BTC/USD", limit=3)
    assert candles[0].market_id == HistoricalDataService.market_id("mock", "BTC/USD")
    assert candles[0].timeframe.value == "1h"
    assert isinstance(candles[0].timestamp, datetime)


def test_repo_load_filters_start_end_and_limit():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    ts0 = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
    rows = [
        {"ts": ts0 + i * 3600, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}
        for i in range(5)
    ]
    repo.upsert("binance", "BTCUSDT", "1h", rows)
    assert len(repo.load("binance", "BTCUSDT", "1h", start_ts=ts0 + 7200, end_ts=ts0 + 14400)) == 3
    assert len(repo.load("binance", "BTCUSDT", "1h", start_ts=ts0 + 7200)) == 3
    limited = repo.load("binance", "BTCUSDT", "1h", limit=2)
    assert len(limited) == 2
    assert limited[0]["ts"] == ts0


def test_repo_count_filters_start_end():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    ts0 = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
    rows = [
        {"ts": ts0 + i * 3600, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}
        for i in range(5)
    ]
    repo.upsert("binance", "BTCUSDT", "1h", rows)
    assert repo.count("binance", "BTCUSDT", "1h", start_ts=ts0 + 7200) == 3
    assert repo.count("binance", "BTCUSDT", "1h", end_ts=ts0 + 7200) == 3
    assert repo.count("binance", "BTCUSDT", "1h", start_ts=ts0, end_ts=ts0 + 14400) == 5


def test_repo_row_accepts_dict_rows():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    row = {"source": "binance", "symbol": "BTCUSDT", "timeframe": "1h", "ts": 1}
    assert repo._row(row) is row


def test_service_rejects_unknown_source():
    service = HistoricalDataService(collectors={})
    try:
        service.fetch("nope", "1h", "BTC/USD")
    except ValueError as exc:
        assert "unknown data source" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_register_and_available_sources():
    service = HistoricalDataService()
    collector = FakeCollector(_rows(1))
    service.register(collector)
    assert service.available_sources() == ["mock"]
    assert service.collectors["mock"] is collector


def test_get_candles_cache_hit_serves_cached_rows_without_refetch():
    conn = _make_conn()
    repo = SQLiteHistoricalCandleRepository(conn)
    now = datetime.now(tz=UTC)
    rows = [
        CollectorOHLCV(
            open=Decimal(100 + i),
            high=Decimal(102 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(1000),
            timestamp=now - timedelta(hours=4 - i),
            symbol="BTC/USD",
        )
        for i in range(5)
    ]
    calls = {"n": 0}

    def counting_fetch(symbol, interval, start=None, end=None, limit=500):
        calls["n"] += 1
        return rows[:limit]

    collector = FakeCollector([])
    collector.fetch_historical = counting_fetch
    service = HistoricalDataService(collectors={"mock": collector}, cache=repo)

    first = service.get_candles("mock", "1h", "BTC/USD", limit=5)
    second = service.get_candles("mock", "1h", "BTC/USD", limit=5)
    assert calls["n"] == 1  # second read served from cache, no refetch
    assert len(first) == 5
    assert len(second) == 5
    truncated = [int(c.timestamp.timestamp()) for c in first]
    assert [int(c.timestamp.timestamp()) for c in second] == truncated


def test_new_migration_v007_present_and_version_7():
    module = importlib.import_module(
        "traderos.infrastructure.database.migrations.v007_historical_candles"
    )
    assert module.VERSION == 7
