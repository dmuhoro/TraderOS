from __future__ import annotations

import uuid
from abc import abstractmethod
from datetime import datetime

from traderos.domain.entities import Signal
from traderos.domain.repositories.base import Repository


class SignalRepository(Repository[Signal]):
    @abstractmethod
    def get_active(self, market_id: uuid.UUID) -> list[Signal]: ...

    @abstractmethod
    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[Signal]: ...

    @abstractmethod
    def get_range(
        self,
        market_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> list[Signal]: ...
