from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod
from datetime import datetime

from traderos.domain.entities import Candle
from traderos.domain.entities import Market
from traderos.domain.repositories.base import Repository


class MarketRepository(Repository[Market]):
    @abstractmethod
    def get_by_symbol(self, symbol: str) -> Market | None: ...

    @abstractmethod
    def list_active(self) -> list[Market]: ...


class CandleRepository(Repository[Candle]):
    @abstractmethod
    def get_range(
        self,
        market_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> list[Candle]: ...

    @abstractmethod
    def get_latest(
        self,
        market_id: uuid.UUID,
        limit: int = 100,
    ) -> list[Candle]: ...

    @abstractmethod
    def delete_by_market(self, market_id: uuid.UUID) -> None: ...


class MarketDataRepository(ABC):
    @abstractmethod
    def get_market(self, symbol: str) -> Market | None: ...

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Candle]: ...

    @abstractmethod
    def save_candle(self, candle: Candle) -> Candle: ...
