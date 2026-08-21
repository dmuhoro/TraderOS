from __future__ import annotations

from typing import Any

from traderos.infrastructure.database.migration_utils import execute

PG = "postgres"

VERSION = 9
DESCRIPTION = "Canonical research + knowledge-graph tables for durable stores"


def up(conn: Any, backend: str = "sqlite") -> None:
    # Experiments + results: the research repos (SQLite/Postgres) read/write
    # these tables; v001 created a legacy research_tests/research_results pair
    # that the current repo contract does not use. Ensure the canonical tables
    # exist on every backend so research data survives restart/crash.
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS experiments (
            id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            results TEXT,
            created_at TEXT NOT NULL
        )
        """,
    )
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS experiment_results (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            metrics TEXT NOT NULL DEFAULT '{}',
            visual_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """,
    )
    # Knowledge graph: nodes + edges (the KnowledgeGraphService repos read/
    # write these). Not created by v001.
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            node_type TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT,
            created_at TEXT NOT NULL
        )
        """,
    )
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL
        )
        """,
    )
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    execute(conn, "DROP TABLE IF EXISTS knowledge_edges")
    execute(conn, "DROP TABLE IF EXISTS knowledge_nodes")
    execute(conn, "DROP TABLE IF EXISTS experiment_results")
    execute(conn, "DROP TABLE IF EXISTS experiments")
    conn.commit()
