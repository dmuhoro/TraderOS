from __future__ import annotations

import uuid

from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.repositories.knowledge_repository import KnowledgeEdgeRepository
from traderos.domain.repositories.knowledge_repository import KnowledgeNodeRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryKnowledgeNodeRepository(InMemoryRepository[KnowledgeNode], KnowledgeNodeRepository):
    def get_by_label(self, label: str) -> list[KnowledgeNode]:
        return [n for n in self.list() if n.label == label]

    def get_by_type(self, node_type: str) -> list[KnowledgeNode]:
        return [n for n in self.list() if n.node_type == node_type]

    def search(self, query: str) -> list[KnowledgeNode]:
        q = query.lower()
        return [n for n in self.list() if q in n.label.lower() or q in n.content.lower()]


class InMemoryKnowledgeEdgeRepository(InMemoryRepository[KnowledgeEdge], KnowledgeEdgeRepository):
    def get_by_source(self, source_id: uuid.UUID) -> list[KnowledgeEdge]:
        return [e for e in self.list() if e.source_id == source_id]

    def get_by_target(self, target_id: uuid.UUID) -> list[KnowledgeEdge]:
        return [e for e in self.list() if e.target_id == target_id]

    def get_neighbors(self, node_id: uuid.UUID, depth: int = 1) -> list[KnowledgeNode]:
        visited: set[uuid.UUID] = {node_id}
        frontier: list[uuid.UUID] = [node_id]

        for _ in range(depth):
            next_frontier: list[uuid.UUID] = []
            for fid in frontier:
                for edge in self._store.values():
                    neighbor = None
                    if edge.source_id == fid and edge.target_id not in visited:
                        neighbor = edge.target_id
                    elif edge.target_id == fid and edge.source_id not in visited:
                        neighbor = edge.source_id
                    if neighbor is not None:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return []
