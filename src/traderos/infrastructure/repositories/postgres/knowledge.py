from __future__ import annotations

import uuid
from typing import Any
from typing import cast

from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.repositories.knowledge_repository import KnowledgeEdgeRepository
from traderos.domain.repositories.knowledge_repository import KnowledgeNodeRepository
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import from_json
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_json
from traderos.infrastructure.repositories.postgres.base import to_uuid


class PostgresKnowledgeNodeRepository(PostgresRepository[KnowledgeNode], KnowledgeNodeRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "knowledge_nodes"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: KnowledgeNode) -> dict:
        return {
            "id": str(entity.id),
            "label": entity.label,
            "node_type": entity.node_type,
            "content": entity.content,
            "embedding": to_json(entity.embedding) if entity.embedding else None,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> KnowledgeNode:
        emb = cast(list[float], from_json(row[4])) if row[4] else None
        return KnowledgeNode(
            id=to_uuid(row[0]),
            label=row[1],
            node_type=row[2],
            content=row[3],
            embedding=emb,
            created_at=to_dt(row[5]),
        )

    def get_by_label(self, label: str) -> list[KnowledgeNode]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM knowledge_nodes WHERE label = %s", (label,))
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_type(self, node_type: str) -> list[KnowledgeNode]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM knowledge_nodes WHERE node_type = %s", (node_type,))
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def search(self, query: str) -> list[KnowledgeNode]:
        like = f"%{query}%"
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM knowledge_nodes WHERE label LIKE %s OR content LIKE %s",
                (like, like),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresKnowledgeEdgeRepository(PostgresRepository[KnowledgeEdge], KnowledgeEdgeRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "knowledge_edges"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: KnowledgeEdge) -> dict:
        return {
            "id": str(entity.id),
            "source_id": str(entity.source_id),
            "target_id": str(entity.target_id),
            "relationship": entity.relationship,
            "weight": entity.weight,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> KnowledgeEdge:
        return KnowledgeEdge(
            id=to_uuid(row[0]),
            source_id=to_uuid(row[1]),
            target_id=to_uuid(row[2]),
            relationship=row[3],
            weight=row[4],
            created_at=to_dt(row[5]),
        )

    def get_by_source(self, source_id: uuid.UUID) -> list[KnowledgeEdge]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM knowledge_edges WHERE source_id = %s",
                (str(source_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_target(self, target_id: uuid.UUID) -> list[KnowledgeEdge]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM knowledge_edges WHERE target_id = %s",
                (str(target_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_neighbors(self, node_id: uuid.UUID, depth: int = 1) -> list[KnowledgeNode]:
        visited: set[str] = {str(node_id)}
        frontier: list[str] = [str(node_id)]

        for _ in range(depth):
            next_frontier: list[str] = []
            ph = ",".join("%s" for _ in frontier)
            sql = (
                f"SELECT * FROM knowledge_edges WHERE source_id IN ({ph})"
                f" OR target_id IN ({ph})"
            )
            with self.conn.cursor() as cur:
                cur.execute(sql, frontier + frontier)
                rows = cur.fetchall()
            for row in rows:
                if row[1] not in visited:
                    visited.add(row[1])
                    next_frontier.append(row[1])
                if row[2] not in visited:
                    visited.add(row[2])
                    next_frontier.append(row[2])
            frontier = next_frontier
        visited.discard(str(node_id))
        if not visited:
            return []
        placeholders = ",".join("%s" for _ in visited)
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM knowledge_nodes WHERE id IN ({placeholders})",
                list(visited),
            )
            rows = cur.fetchall()
        result: list[KnowledgeNode] = []
        for r in rows:
            emb = cast(list[float], from_json(r[4])) if r[4] else None
            result.append(
                KnowledgeNode(
                    id=to_uuid(r[0]),
                    label=r[1],
                    node_type=r[2],
                    content=r[3],
                    embedding=emb,
                    created_at=to_dt(r[5]),
                )
            )
        return result
