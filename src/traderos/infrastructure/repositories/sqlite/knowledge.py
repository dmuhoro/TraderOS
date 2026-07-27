from __future__ import annotations

import sqlite3
import uuid
from typing import cast

from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.repositories.knowledge_repository import KnowledgeEdgeRepository
from traderos.domain.repositories.knowledge_repository import KnowledgeNodeRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import from_json
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_json
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteKnowledgeNodeRepository(SQLiteRepository[KnowledgeNode], KnowledgeNodeRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "knowledge_nodes"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: KnowledgeNode) -> dict:
        return {
            "id": str(entity.id),
            "label": entity.label,
            "node_type": entity.node_type,
            "content": entity.content,
            "embedding": to_json(entity.embedding) if entity.embedding else None,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> KnowledgeNode:
        emb = cast(list[float], from_json(row["embedding"])) if row["embedding"] else None
        return KnowledgeNode(
            id=to_uuid(row["id"]),
            label=row["label"],
            node_type=row["node_type"],
            content=row["content"],
            embedding=emb,
            created_at=to_dt(row["created_at"]),
        )

    def get_by_label(self, label: str) -> list[KnowledgeNode]:
        cursor = self.conn.execute("SELECT * FROM knowledge_nodes WHERE label = ?", (label,))
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_by_type(self, node_type: str) -> list[KnowledgeNode]:
        cursor = self.conn.execute(
            "SELECT * FROM knowledge_nodes WHERE node_type = ?", (node_type,)
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def search(self, query: str) -> list[KnowledgeNode]:
        like = f"%{query}%"
        cursor = self.conn.execute(
            "SELECT * FROM knowledge_nodes WHERE label LIKE ? OR content LIKE ?",
            (like, like),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteKnowledgeEdgeRepository(SQLiteRepository[KnowledgeEdge], KnowledgeEdgeRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "knowledge_edges"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: KnowledgeEdge) -> dict:
        return {
            "id": str(entity.id),
            "source_id": str(entity.source_id),
            "target_id": str(entity.target_id),
            "relationship": entity.relationship,
            "weight": entity.weight,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> KnowledgeEdge:
        return KnowledgeEdge(
            id=to_uuid(row["id"]),
            source_id=to_uuid(row["source_id"]),
            target_id=to_uuid(row["target_id"]),
            relationship=row["relationship"],
            weight=row["weight"],
            created_at=to_dt(row["created_at"]),
        )

    def get_by_source(self, source_id: uuid.UUID) -> list[KnowledgeEdge]:
        cursor = self.conn.execute(
            "SELECT * FROM knowledge_edges WHERE source_id = ?",
            (str(source_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_by_target(self, target_id: uuid.UUID) -> list[KnowledgeEdge]:
        cursor = self.conn.execute(
            "SELECT * FROM knowledge_edges WHERE target_id = ?",
            (str(target_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_neighbors(self, node_id: uuid.UUID, depth: int = 1) -> list[KnowledgeNode]:
        visited: set[str] = {str(node_id)}
        frontier: list[str] = [str(node_id)]

        for _ in range(depth):
            next_frontier: list[str] = []
            ph = ",".join("?" for _ in frontier)
            sql = f"SELECT * FROM knowledge_edges WHERE source_id IN ({ph}) OR target_id IN ({ph})"
            cursor = self.conn.execute(sql, frontier + frontier)
            for row in cursor.fetchall():
                if row["source_id"] not in visited:
                    visited.add(row["source_id"])
                    next_frontier.append(row["source_id"])
                if row["target_id"] not in visited:
                    visited.add(row["target_id"])
                    next_frontier.append(row["target_id"])
            frontier = next_frontier
        visited.discard(str(node_id))
        if not visited:
            return []
        placeholders = ",".join("?" for _ in visited)
        cursor = self.conn.execute(
            f"SELECT * FROM knowledge_nodes WHERE id IN ({placeholders})",
            list(visited),
        )
        result: list[KnowledgeNode] = []
        for r in cursor.fetchall():
            emb = cast(list[float], from_json(r["embedding"])) if r["embedding"] else None
            result.append(
                KnowledgeNode(
                    id=to_uuid(r["id"]),
                    label=r["label"],
                    node_type=r["node_type"],
                    content=r["content"],
                    embedding=emb,
                    created_at=to_dt(r["created_at"]),
                )
            )
        return result
