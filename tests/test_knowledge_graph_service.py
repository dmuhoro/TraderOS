from __future__ import annotations

import uuid

from traderos.domain.services.knowledge_graph_service import KnowledgeGraphService
from traderos.infrastructure.repositories.in_memory.knowledge import InMemoryKnowledgeEdgeRepository
from traderos.infrastructure.repositories.in_memory.knowledge import InMemoryKnowledgeNodeRepository


def _make_svc() -> KnowledgeGraphService:
    return KnowledgeGraphService(
        nodes=InMemoryKnowledgeNodeRepository(),
        edges=InMemoryKnowledgeEdgeRepository(),
    )


class TestKnowledgeGraphService:
    def test_add_node(self) -> None:
        svc = _make_svc()
        node = svc.add_node("BTC Pattern", "observation", "Saw a recurring pattern")
        assert node.label == "BTC Pattern"
        assert node.node_type == "observation"

    def test_add_edge(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "type", "content A")
        b = svc.add_node("B", "type", "content B")
        edge = svc.add_edge(a.id, b.id, "relates_to", 1.0)
        assert edge.source_id == a.id
        assert edge.target_id == b.id
        assert edge.relationship == "relates_to"

    def test_get_neighbors(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "t", "A")
        b = svc.add_node("B", "t", "B")
        c = svc.add_node("C", "t", "C")
        svc.add_edge(a.id, b.id, "to")
        svc.add_edge(a.id, c.id, "to")
        neighbors = svc.get_neighbors(a.id, 1)
        assert len(neighbors) == 2

    def test_bfs_traversal(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "t", "A")
        b = svc.add_node("B", "t", "B")
        c = svc.add_node("C", "t", "C")
        d = svc.add_node("D", "t", "D")
        svc.add_edge(a.id, b.id, "to")
        svc.add_edge(b.id, c.id, "to")
        svc.add_edge(c.id, d.id, "to")
        levels = svc.traverse_bfs(a.id, 3)
        assert len(levels) >= 3

    def test_find_path(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "t", "A")
        b = svc.add_node("B", "t", "B")
        c = svc.add_node("C", "t", "C")
        svc.add_edge(a.id, b.id, "to")
        svc.add_edge(b.id, c.id, "to")
        path = svc.find_path(a.id, c.id)
        assert len(path) == 3
        assert path[0].id == a.id
        assert path[2].id == c.id

    def test_find_path_no_path(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "t", "A")
        b = svc.add_node("B", "t", "B")
        path = svc.find_path(a.id, b.id)
        assert path == []

    def test_search(self) -> None:
        svc = _make_svc()
        svc.add_node("BTC", "symbol", "Bitcoin pattern")
        svc.add_node("ETH", "symbol", "Ethereum pattern")
        results = svc.search("pattern")
        assert all("pattern" in n.content for n in results)

    def test_auto_index(self) -> None:
        svc = _make_svc()
        nid = uuid.uuid4()
        node = svc.auto_index_research(nid, "Obs-1", "observation", "Saw a thing")
        assert node.label == "Obs-1"
        assert node.node_type == "observation"

    def test_find_insights(self) -> None:
        svc = _make_svc()
        a = svc.add_node("Hub", "t", "hub")
        for i in range(4):
            n = svc.add_node(f"N{i}", "t", f"n{i}")
            svc.add_edge(a.id, n.id, "connects")
        insights = svc.find_insights(min_connections=3)
        assert len(insights) == 1
        assert insights[0][0].id == a.id

    def test_bfs_respects_max_depth(self) -> None:
        svc = _make_svc()
        nodes = []
        for i in range(5):
            n = svc.add_node(f"N{i}", "t", f"n{i}")
            if nodes:
                svc.add_edge(nodes[-1].id, n.id, "to")
            nodes.append(n)
        levels = svc.traverse_bfs(nodes[0].id, 3)
        seen = {node.id for level in levels for node in level}
        assert nodes[0].id not in seen  # start node not re-visited
        assert nodes[4].id not in seen  # depth 4 pruned by break
        assert nodes[3].id in seen  # depth 3 kept

    def test_find_path_same_node(self) -> None:
        svc = _make_svc()
        a = svc.add_node("A", "t", "A")
        path = svc.find_path(a.id, a.id)
        assert len(path) == 1
        assert path[0].id == a.id
