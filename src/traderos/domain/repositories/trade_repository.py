from __future__ import annotations

import uuid
from abc import abstractmethod

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.repositories.base import Repository


class TradeRepository(Repository[Trade]):
    @abstractmethod
    def get_by_signal(self, signal_id: uuid.UUID) -> list[Trade]: ...

    @abstractmethod
    def get_by_market(self, market_id: uuid.UUID) -> list[Trade]: ...

    @abstractmethod
    def get_open(self) -> list[Trade]: ...


class PositionRepository(Repository[Position]):
    @abstractmethod
    def get_by_market(self, market_id: uuid.UUID) -> Position | None: ...

    @abstractmethod
    def list_open(self) -> list[Position]: ...
