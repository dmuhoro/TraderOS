from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod
from typing import Generic
from typing import Protocol
from typing import TypeVar


class EntityProtocol(Protocol):
    @property
    def id(self) -> uuid.UUID: ...


T = TypeVar("T", bound=EntityProtocol)


class Repository(ABC, Generic[T]):
    @abstractmethod
    def add(self, entity: T) -> T: ...

    @abstractmethod
    def get(self, entity_id: uuid.UUID) -> T | None: ...

    @abstractmethod
    def list(self) -> list[T]: ...

    @abstractmethod
    def update(self, entity: T) -> T: ...

    @abstractmethod
    def delete(self, entity_id: uuid.UUID) -> None: ...
