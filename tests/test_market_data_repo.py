from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import AssetClass
from traderos.domain.entities import Candle
from traderos.domain.entities import Market
from traderos.domain.entities import Timeframe
from traderos.infrastructure.repositories.in_memory.market_data import InMemoryMarketDataRepository


class TestMarketDataRepository:
    def test_get_market_by_symbol(self) -> None:
        repo = InMemoryMarketDataRepository()
        market = Market(symbol="BTCUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")
        repo._markets.add(market)
        found = repo.get_market("BTCUSDT")
        assert found is not None
        assert found.symbol == "BTCUSDT"

    def test_get_market_nonexistent_returns_none(self) -> None:
        repo = InMemoryMarketDataRepository()
        assert repo.get_market("NONEXIST") is None

    def test_get_candles_without_market_returns_empty(self) -> None:
        repo = InMemoryMarketDataRepository()
        candles = repo.get_candles("NONEXIST")
        assert candles == []

    def test_get_candles_with_market(self) -> None:
        repo = InMemoryMarketDataRepository()
        market = Market(symbol="BTCUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")
        repo._markets.add(market)
        candle = Candle(
            market_id=market.id,
            ohlcv=OHLCV(Decimal(100), Decimal(105), Decimal(99), Decimal(102), Decimal(1000)),
            timestamp=datetime.now(tz=UTC),
            timeframe=Timeframe.HOUR_1,
        )
        repo.save_candle(candle)
        candles = repo.get_candles("BTCUSDT")
        assert len(candles) == 1

    def test_save_and_get_candle_roundtrip(self) -> None:
        repo = InMemoryMarketDataRepository()
        market = Market(symbol="ETHUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")
        repo._markets.add(market)
        candle = Candle(
            market_id=market.id,
            ohlcv=OHLCV(Decimal(2000), Decimal(2010), Decimal(1990), Decimal(2005), Decimal(5000)),
            timestamp=datetime.now(tz=UTC),
            timeframe=Timeframe.HOUR_1,
        )
        saved = repo.save_candle(candle)
        assert saved.id is not None
