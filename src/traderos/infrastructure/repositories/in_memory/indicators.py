from __future__ import annotations

import uuid

from traderos.domain.entities import Indicator
from traderos.domain.entities import LiquidityZone
from traderos.domain.repositories.indicator_repository import IndicatorRepository
from traderos.domain.repositories.liquidity_repository import LiquidityZoneRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryIndicatorRepository(InMemoryRepository[Indicator], IndicatorRepository):
    def get_by_name(self, market_id: uuid.UUID, name: str) -> list[Indicator]:
        return [i for i in self.list() if i.market_id == market_id and i.name == name]

    def get_latest(self, market_id: uuid.UUID, name: str) -> Indicator | None:
        indicators = self.get_by_name(market_id, name)
        if not indicators:
            return None
        indicators.sort(key=lambda i: i.timestamp, reverse=True)
        return indicators[0]


class InMemoryLiquidityZoneRepository(InMemoryRepository[LiquidityZone], LiquidityZoneRepository):
    pass
