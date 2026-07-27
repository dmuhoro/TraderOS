from __future__ import annotations

import uuid
from abc import abstractmethod

from traderos.domain.entities import Indicator
from traderos.domain.repositories.base import Repository


class IndicatorRepository(Repository[Indicator]):
    @abstractmethod
    def get_by_name(self, market_id: uuid.UUID, name: str) -> list[Indicator]: ...

    @abstractmethod
    def get_latest(self, market_id: uuid.UUID, name: str) -> Indicator | None: ...
