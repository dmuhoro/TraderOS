from __future__ import annotations

import uuid
from datetime import datetime

from traderos.domain.entities import Candle
from traderos.domain.entities import Market
from traderos.domain.repositories.market_data_repository import CandleRepository
from traderos.domain.repositories.market_data_repository import MarketDataRepository
from traderos.domain.repositories.market_data_repository import MarketRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryMarketRepository(InMemoryRepository[Market], MarketRepository):
    def get_by_symbol(self, symbol: str) -> Market | None:
        for market in self._store.values():
            if market.symbol == symbol:
                from copy import deepcopy

                return deepcopy(market)
        return None

    def list_active(self) -> list[Market]:
        return [m for m in self.list() if m.status.name == "ACTIVE"]


class InMemoryCandleRepository(InMemoryRepository[Candle], CandleRepository):
    def get_range(self, market_id: uuid.UUID, start: datetime, end: datetime) -> list[Candle]:
        return [c for c in self.list() if c.market_id == market_id and start <= c.timestamp <= end]

    def get_latest(self, market_id: uuid.UUID, limit: int = 100) -> list[Candle]:
        candles = [c for c in self.list() if c.market_id == market_id]
        candles.sort(key=lambda c: c.timestamp, reverse=True)
        return candles[:limit]

    def delete_by_market(self, market_id: uuid.UUID) -> None:
        to_delete = [cid for cid, c in self._store.items() if c.market_id == market_id]
        for cid in to_delete:
            self._store.pop(cid, None)


class InMemoryMarketDataRepository(MarketDataRepository):
    def __init__(
        self,
        market_repo: MarketRepository | None = None,
        candle_repo: CandleRepository | None = None,
    ) -> None:
        self._markets = market_repo or InMemoryMarketRepository()
        self._candles = candle_repo or InMemoryCandleRepository()

    def get_market(self, symbol: str) -> Market | None:
        return self._markets.get_by_symbol(symbol)

    def get_candles(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Candle]:
        market = self._markets.get_by_symbol(symbol)
        if market is None:
            return []
        if start and end:
            return self._candles.get_range(market.id, start, end)
        return self._candles.get_latest(market.id, limit)

    def save_candle(self, candle: Candle) -> Candle:
        return self._candles.add(candle)
