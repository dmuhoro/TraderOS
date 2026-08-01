from __future__ import annotations

from typing import Any

from traderos.infrastructure.database.migration_utils import execute

PG = "postgres"

VERSION = 6
DESCRIPTION = "Operator surface: workflow state tables + strategies table reconcile"


_REPO_STRATEGIES_DDL = """
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE,
        params TEXT NOT NULL DEFAULT '{}',
        version TEXT NOT NULL DEFAULT '1.0.0',
        status TEXT NOT NULL DEFAULT 'draft',
        template TEXT,
        created_at TEXT NOT NULL
    )
"""


def _serial(backend: str) -> str:
    return "SERIAL PRIMARY KEY" if backend == PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _table_exists(conn: Any, backend: str, table: str) -> bool:
    if backend == PG:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (table,))
        return cur.fetchone()[0] is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _is_legacy_strategies(conn: Any, backend: str) -> bool:
    """True when ``strategies`` still uses the legacy v001 schema.

    v001 declared ``id INTEGER`` and ``params_json`` — incompatible with the
    repository layer (UUID string ids, ``params`` JSON column). Detected by
    the presence of the legacy column or an integer id column.
    """
    if backend == PG:
        cur = conn.cursor()
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'strategies' AND column_name = 'id'"
        )
        row = cur.fetchone()
        return row is not None and "int" in row[0].lower()
    rows = conn.execute("PRAGMA table_info(strategies)").fetchall()
    if not rows:
        return False
    columns = {r[1]: (r[2] or "").upper() for r in rows}
    return "params_json" in columns or "INTEGER" in columns.get("id", "")


def up(conn: Any, backend: str = "sqlite") -> None:
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS operator_workflow (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_step TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            session_id TEXT,
            started_at TEXT,
            completed_at TEXT
        )
    """,
    )
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS workflow_transitions (
            id {_serial(backend)},
            workflow_id INTEGER NOT NULL,
            from_step TEXT,
            to_step TEXT NOT NULL,
            actor TEXT NOT NULL,
            result TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """,
    )

    # The repository layer owns the canonical `strategies` schema (id TEXT,
    # params, template, version, status). v001 historically created a legacy
    # integer-id `strategies` table (superseded; no longer created on fresh
    # builds). Rebuild any legacy table to the repo schema, and otherwise make
    # sure the canonical table exists (fresh SQLite and PostgreSQL builds).
    if _table_exists(conn, backend, "strategies") and _is_legacy_strategies(conn, backend):
        # The legacy v001 `backtest_results` FK references strategies(id).
        # PostgreSQL enforces FKs, so the legacy child table must be dropped
        # before the parent can be rebuilt. The legacy tables were never
        # populated through the application (repos create their own schema),
        # so this is lossless.
        if backend == PG:
            execute(conn, "DROP TABLE IF EXISTS backtest_results")
        execute(conn, "ALTER TABLE strategies RENAME TO strategies_legacy")
        execute(conn, _REPO_STRATEGIES_DDL)
        execute(
            conn,
            """
            INSERT INTO strategies (id, name, params, version, status, created_at)
            SELECT CAST(id AS TEXT), name, COALESCE(params_json, '{}'), '1.0.0',
                   'draft', COALESCE(created_at, CURRENT_TIMESTAMP)
            FROM strategies_legacy
            """,
        )
        execute(conn, "DROP TABLE strategies_legacy")
    else:
        execute(conn, _REPO_STRATEGIES_DDL)
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    execute(conn, "DROP TABLE IF EXISTS workflow_transitions")
    execute(conn, "DROP TABLE IF EXISTS operator_workflow")
    conn.commit()
