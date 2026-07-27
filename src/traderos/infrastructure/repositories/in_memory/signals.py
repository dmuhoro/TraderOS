from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.entities import Signal
from traderos.domain.repositories.signal_repository import SignalRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemorySignalRepository(InMemoryRepository[Signal], SignalRepository):
    def get_active(self, market_id: uuid.UUID) -> list[Signal]:
        now = datetime.now(tz=UTC)
        return [s for s in self.list() if s.market_id == market_id and s.expires_at > now]

    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[Signal]:
        return [s for s in self.list() if s.strategy_id == strategy_id]

    def get_range(self, market_id: uuid.UUID, start: datetime, end: datetime) -> list[Signal]:
        return [
            s for s in self.list() if s.market_id == market_id and start <= s.generated_at <= end
        ]
