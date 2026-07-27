from __future__ import annotations

import uuid
from copy import deepcopy

from traderos.domain.repositories.base import Repository
from traderos.domain.repositories.base import T


class InMemoryRepository(Repository[T]):
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, T] = {}

    def add(self, entity: T) -> T:
        self._store[entity.id] = deepcopy(entity)
        return deepcopy(entity)

    def get(self, entity_id: uuid.UUID) -> T | None:
        entity = self._store.get(entity_id)
        return deepcopy(entity) if entity is not None else None

    def list(self) -> list[T]:
        return [deepcopy(e) for e in self._store.values()]

    def update(self, entity: T) -> T:
        self._store[entity.id] = deepcopy(entity)
        return deepcopy(entity)

    def delete(self, entity_id: uuid.UUID) -> None:
        self._store.pop(entity_id, None)
