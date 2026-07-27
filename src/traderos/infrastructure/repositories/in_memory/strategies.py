from __future__ import annotations

import uuid

from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Strategy
from traderos.domain.repositories.strategy_repository import BacktestResultRepository
from traderos.domain.repositories.strategy_repository import StrategyRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryStrategyRepository(InMemoryRepository[Strategy], StrategyRepository):
    def get_by_name(self, name: str) -> Strategy | None:
        for strategy in self._store.values():
            if strategy.name == name:
                from copy import deepcopy

                return deepcopy(strategy)
        return None

    def list_active(self) -> list[Strategy]:
        return [s for s in self.list() if s.status.name == "ACTIVE"]


class InMemoryBacktestResultRepository(
    InMemoryRepository[BacktestResult], BacktestResultRepository
):
    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[BacktestResult]:
        return [r for r in self.list() if r.strategy_id == strategy_id]

    def get_by_market(self, market_id: uuid.UUID) -> list[BacktestResult]:
        return [r for r in self.list() if r.market_id == market_id]
