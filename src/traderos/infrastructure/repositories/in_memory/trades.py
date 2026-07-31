from __future__ import annotations

import uuid

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities.trade import OPEN_TRADE_STATUSES
from traderos.domain.repositories.trade_repository import PositionRepository
from traderos.domain.repositories.trade_repository import TradeRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryTradeRepository(InMemoryRepository[Trade], TradeRepository):
    def get_by_signal(self, signal_id: uuid.UUID) -> list[Trade]:
        return [t for t in self.list() if t.signal_id == signal_id]

    def get_by_market(self, market_id: uuid.UUID) -> list[Trade]:
        return [t for t in self.list() if t.market_id == market_id]

    def get_open(self) -> list[Trade]:
        return [t for t in self.list() if t.status in OPEN_TRADE_STATUSES]


class InMemoryPositionRepository(InMemoryRepository[Position], PositionRepository):
    def get_by_market(self, market_id: uuid.UUID) -> Position | None:
        for pos in self._store.values():
            if pos.market_id == market_id:
                from copy import deepcopy

                return deepcopy(pos)
        return None

    def list_open(self) -> list[Position]:
        return [p for p in self.list() if abs(p.quantity) > 0]
