from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass

from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.repositories import KnowledgeEdgeRepository
from traderos.domain.repositories import KnowledgeNodeRepository


@dataclass
class KnowledgeGraphService:
    nodes: KnowledgeNodeRepository
    edges: KnowledgeEdgeRepository

    def add_node(self, label: str, node_type: str, content: str) -> KnowledgeNode:
        node = KnowledgeNode(label=label, node_type=node_type, content=content)
        return self.nodes.add(node)

    def add_edge(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        relationship: str,
        weight: float = 1.0,
    ) -> KnowledgeEdge:
        edge = KnowledgeEdge(
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            weight=weight,
        )
        return self.edges.add(edge)

    def get_neighbors(self, node_id: uuid.UUID, depth: int = 1) -> list[KnowledgeNode]:
        all_edges = self.edges.list()
        neighbor_ids: set[uuid.UUID] = set()
        frontier: set[uuid.UUID] = {node_id}
        for _ in range(depth):
            next_frontier: set[uuid.UUID] = set()
            for fid in frontier:
                for edge in all_edges:
                    is_out = edge.source_id == fid
                    is_in = edge.target_id == fid
                    if is_out and edge.target_id not in neighbor_ids and edge.target_id != node_id:
                        next_frontier.add(edge.target_id)
                    elif is_in and edge.source_id not in neighbor_ids and edge.source_id != node_id:
                        next_frontier.add(edge.source_id)
            neighbor_ids.update(next_frontier)
            frontier = next_frontier
        resolved = []
        for nid in neighbor_ids:
            node = self.nodes.get(nid)
            if node:
                resolved.append(node)
        return resolved

    def traverse_bfs(
        self,
        start_id: uuid.UUID,
        max_depth: int = 5,
    ) -> list[list[KnowledgeNode]]:
        levels: list[list[KnowledgeNode]] = []
        visited: set[uuid.UUID] = {start_id}
        queue: deque[tuple[uuid.UUID, int]] = deque([(start_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth > max_depth:
                break
            nbrs = self.get_neighbors(current_id, 1)
            for nbr in nbrs:
                if nbr.id not in visited:
                    visited.add(nbr.id)
                    if depth + 1 <= max_depth:
                        while len(levels) <= depth + 1:
                            levels.append([])
                        levels[depth + 1].append(nbr)
                    queue.append((nbr.id, depth + 1))

        return levels

    def find_path(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        max_depth: int = 10,
    ) -> list[KnowledgeNode]:
        if source_id == target_id:
            start = self.nodes.get(source_id)
            return [start] if start else []

        visited: set[uuid.UUID] = {source_id}
        parent: dict[uuid.UUID, uuid.UUID] = {}
        queue: deque[uuid.UUID] = deque([source_id])

        while queue:
            current_id = queue.popleft()
            nbrs = self.get_neighbors(current_id, 1)
            for nbr in nbrs:
                if nbr.id not in visited:
                    visited.add(nbr.id)
                    parent[nbr.id] = current_id
                    if nbr.id == target_id:
                        path: list[KnowledgeNode] = []
                        node_id = target_id
                        while node_id != source_id:
                            node = self.nodes.get(node_id)
                            if node:
                                path.append(node)
                            node_id = parent[node_id]
                        start_node = self.nodes.get(source_id)
                        if start_node:
                            path.append(start_node)
                        path.reverse()
                        return path
                    queue.append(nbr.id)

        return []

    def search(self, query: str) -> list[KnowledgeNode]:
        return self.nodes.search(query)

    def auto_index_research(
        self,
        entity_id: uuid.UUID,
        label: str,
        entity_type: str,
        content: str,
    ) -> KnowledgeNode:
        return self.add_node(
            label=label,
            node_type=entity_type,
            content=content,
        )

    def find_insights(
        self,
        min_connections: int = 3,
    ) -> list[tuple[KnowledgeNode, list[KnowledgeEdge]]]:
        all_nodes = self.nodes.list()
        insights: list[tuple[KnowledgeNode, list[KnowledgeEdge]]] = []
        for node in all_nodes:
            edges_to = self.edges.get_by_target(node.id)
            edges_from = self.edges.get_by_source(node.id)
            total = len(edges_to) + len(edges_from)
            if total >= min_connections:
                insights.append((node, edges_to + edges_from))
        return sorted(insights, key=lambda x: len(x[1]), reverse=True)
