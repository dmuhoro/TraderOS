from __future__ import annotations

import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import AssetClass
from traderos.domain.entities import Candle
from traderos.domain.entities import Market
from traderos.domain.entities import MarketStatus
from traderos.domain.entities import Timeframe
from traderos.infrastructure.repositories.sqlite.markets import SQLiteCandleRepository
from traderos.infrastructure.repositories.sqlite.markets import SQLiteMarketDataRepository
from traderos.infrastructure.repositories.sqlite.markets import SQLiteMarketRepository


def _market(symbol: str = "BTCUSDT", status: MarketStatus = MarketStatus.ACTIVE) -> Market:
    return Market(
        symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        exchange="BINANCE",
        status=status,
    )


def _candle(market_id, ts: datetime) -> Candle:
    return Candle(
        market_id=market_id,
        ohlcv=OHLCV(Decimal(100), Decimal(105), Decimal(99), Decimal(102), Decimal(1000)),
        timestamp=ts,
        timeframe=Timeframe.HOUR_1,
    )


class TestSQLiteMarketRepository:
    def test_add_and_get_by_symbol(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketRepository(conn)
        m = repo.add(_market())
        found = repo.get_by_symbol("BTCUSDT")
        assert found is not None
        assert found.id == m.id
        assert found.exchange == "BINANCE"
        assert found.status == MarketStatus.ACTIVE

    def test_get_by_symbol_missing_returns_none(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketRepository(conn)
        assert repo.get_by_symbol("NOPE") is None

    def test_list_active_filters_inactive(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketRepository(conn)
        repo.add(_market("BTCUSDT"))
        repo.add(_market("ETHUSDT", status=MarketStatus.INACTIVE))
        active = repo.list_active()
        assert [m.symbol for m in active] == ["BTCUSDT"]


class TestSQLiteCandleRepository:
    def test_get_range_bounds_inclusive(self) -> None:
        conn = sqlite3.connect(":memory:")
        market = SQLiteMarketRepository(conn).add(_market())
        repo = SQLiteCandleRepository(conn)
        start = datetime(2024, 1, 1, tzinfo=UTC)
        repo.add(_candle(market.id, start))
        repo.add(_candle(market.id, start + timedelta(hours=2)))
        in_range = repo.get_range(market.id, start, start + timedelta(hours=1))
        assert len(in_range) == 1
        assert in_range[0].timestamp == start

    def test_get_latest_orders_desc_and_limits(self) -> None:
        conn = sqlite3.connect(":memory:")
        market = SQLiteMarketRepository(conn).add(_market())
        repo = SQLiteCandleRepository(conn)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(3):
            repo.add(_candle(market.id, base + timedelta(hours=i)))
        latest = repo.get_latest(market.id, limit=2)
        assert len(latest) == 2
        assert latest[0].timestamp == base + timedelta(hours=2)

    def test_delete_by_market_removes_candles(self) -> None:
        conn = sqlite3.connect(":memory:")
        market = SQLiteMarketRepository(conn).add(_market())
        repo = SQLiteCandleRepository(conn)
        repo.add(_candle(market.id, datetime(2024, 1, 1, tzinfo=UTC)))
        repo.delete_by_market(market.id)
        assert repo.get_latest(market.id) == []


class TestSQLiteMarketDataRepository:
    def test_get_market_delegates_to_market_repo(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketDataRepository(conn)
        market = repo._markets.add(_market("ETHUSDT"))
        assert repo.get_market("ETHUSDT").id == market.id
        assert repo.get_market("NOPE") is None

    def test_get_candles_missing_market_returns_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketDataRepository(conn)
        assert repo.get_candles("NOPE") == []

    def test_get_candles_range_when_bounds_given(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketDataRepository(conn)
        market = repo._markets.add(_market())
        start = datetime(2024, 1, 1, tzinfo=UTC)
        repo.save_candle(_candle(market.id, start))
        candles = repo.get_candles("BTCUSDT", start=start, end=start)
        assert len(candles) == 1

    def test_get_candles_latest_when_no_bounds(self) -> None:
        conn = sqlite3.connect(":memory:")
        repo = SQLiteMarketDataRepository(conn)
        market = repo._markets.add(_market())
        repo.save_candle(_candle(market.id, datetime(2024, 1, 1, tzinfo=UTC)))
        candles = repo.get_candles("BTCUSDT")
        assert len(candles) == 1
