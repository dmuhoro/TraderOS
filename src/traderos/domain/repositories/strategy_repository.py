from __future__ import annotations

import uuid
from abc import abstractmethod

from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Strategy
from traderos.domain.repositories.base import Repository


class StrategyRepository(Repository[Strategy]):
    @abstractmethod
    def get_by_name(self, name: str) -> Strategy | None: ...

    @abstractmethod
    def list_active(self) -> list[Strategy]: ...


class BacktestResultRepository(Repository[BacktestResult]):
    @abstractmethod
    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[BacktestResult]: ...

    @abstractmethod
    def get_by_market(self, market_id: uuid.UUID) -> list[BacktestResult]: ...
