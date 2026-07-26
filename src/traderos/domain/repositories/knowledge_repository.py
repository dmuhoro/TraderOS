from __future__ import annotations

import uuid
from abc import abstractmethod

from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.repositories.base import Repository


class KnowledgeNodeRepository(Repository[KnowledgeNode]):
    @abstractmethod
    def get_by_label(self, label: str) -> list[KnowledgeNode]: ...

    @abstractmethod
    def get_by_type(self, node_type: str) -> list[KnowledgeNode]: ...

    @abstractmethod
    def search(self, query: str) -> list[KnowledgeNode]: ...


class KnowledgeEdgeRepository(Repository[KnowledgeEdge]):
    @abstractmethod
    def get_by_source(self, source_id: uuid.UUID) -> list[KnowledgeEdge]: ...

    @abstractmethod
    def get_by_target(self, target_id: uuid.UUID) -> list[KnowledgeEdge]: ...

    @abstractmethod
    def get_neighbors(self, node_id: uuid.UUID, depth: int = 1) -> list[KnowledgeNode]: ...
