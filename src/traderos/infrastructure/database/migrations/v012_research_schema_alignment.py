"""Align research tables with the repository DDL on databases that predate it.

v001 originally declared observations/hypotheses/research_tests/research_results/
lessons with INTEGER surrogate keys, a ``timestamp`` column defaulting to
CURRENT_TIMESTAMP, and foreign keys. The repositories have always written TEXT
uuid keys and ISO-8601 TEXT timestamps, and the domain entities are uuid.UUID.
Editing v001 fixes fresh databases only -- every database that already recorded
v001 as applied keeps the old shape forever, because the version marker means
v001 is never re-run. On such a database the repositories fail on insert with a
DatatypeMismatch, and ResearchEngine's inserts fail on the NOT NULL timestamp.

This migration rebuilds the five tables to match the repository DDL exactly,
which is the same shape v001 now declares. It is a no-op on an already-aligned
database, so it is safe to run against both fresh and pre-existing databases.

Data is preserved by mapping the old integer ids to TEXT uuids and rewriting the
child columns that referenced them.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.infrastructure.database.migration_utils import PG
from traderos.infrastructure.database.migration_utils import detect_backend
from traderos.infrastructure.database.migration_utils import execute

VERSION = 12
DESCRIPTION = "Align legacy research tables with the repository TEXT-uuid DDL"

# These tables are rebuilt, not created: they predate this migration. An existing
# database that never ran v001 gets them from v001, which now declares the
# canonical shape, and the rebuild below then finds nothing to do.
TABLES: tuple[str, ...] = ()

# (table, new DDL, child columns holding a reference to it). Order matters:
# parents are rebuilt before children so the child rebuild can read the parent's
# new uuid mapping.
_SPEC: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "observations",
        """CREATE TABLE {t} (
                id TEXT PRIMARY KEY NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]'
            )""",
        (),
    ),
    (
        "hypotheses",
        """CREATE TABLE {t} (
                id TEXT PRIMARY KEY NOT NULL,
                observation_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_at TEXT NOT NULL
            )""",
        ("observation_id",),
    ),
    (
        "research_tests",
        """CREATE TABLE {t} (
                id TEXT PRIMARY KEY NOT NULL,
                hypothesis_id TEXT NOT NULL,
                test_params TEXT,
                results_summary TEXT
            )""",
        ("hypothesis_id",),
    ),
    (
        "research_results",
        """CREATE TABLE {t} (
                id TEXT PRIMARY KEY NOT NULL,
                test_id TEXT NOT NULL,
                metrics_json TEXT,
                visual_path TEXT
            )""",
        ("test_id",),
    ),
    (
        "lessons",
        """CREATE TABLE {t} (
                id TEXT PRIMARY KEY NOT NULL,
                result_id TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )""",
        ("result_id",),
    ),
)


def _table_exists(conn: Any, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    )


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: str(row[2]).upper() for row in conn.execute(f"PRAGMA table_info({table})")}


def _sqlite_needs_rebuild(conn: sqlite3.Connection, table: str) -> bool:
    """True when the table exists with the legacy integer-keyed shape."""
    if not _table_exists(conn, table):
        return False  # v001 already declared the canonical shape.
    columns = _sqlite_columns(conn, table)
    if not columns:
        return False
    id_type = columns.get("id", "")
    if "INT" in id_type and "TEXT" not in id_type:
        return True
    # Already TEXT-keyed, but the legacy ``timestamp`` column is present where the
    # canonical shape uses created_at (hypotheses, lessons).
    return "timestamp" in columns and "created_at" not in columns and table != "observations"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def read_column(row: Any, pos: dict[str, int], name: str) -> Any:
    """Value of ``name`` in ``row``, or None when the legacy table lacks it."""
    index = pos.get(name)
    return row[index] if index is not None and index < len(row) else None


def _positions(columns: dict[str, str]) -> dict[str, int]:
    """Positional index of each legacy column, so a name lookup cannot silently
    read the neighbouring field. Plain sqlite3 rows are tuples, so ``row[name]``
    raises and every lookup would otherwise fall back to a hardcoded index."""
    return {name: index for index, name in enumerate(columns)}


def _sqlite_rebuild(
    conn: sqlite3.Connection,
    table: str,
    ddl: str,
    refs: tuple[str, ...],
    parent_maps: dict[str, dict[Any, str]],
) -> None:
    """Rename-and-copy rebuild, the pattern v010 uses for SQLite money types."""
    columns = _sqlite_columns(conn, table)
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? "
            "AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    ]
    old_table = f"{table}__v012_old"
    conn.execute(f"ALTER TABLE {table} RENAME TO {old_table}")
    conn.execute(ddl.format(t=table))

    # Old integer id -> new TEXT uuid. Children read this so their reference
    # columns point at the ids that were actually inserted above.
    id_map: dict[Any, str] = {}
    new_names = [name.strip().split()[0] for name in _new_column_names(ddl)]
    rows = conn.execute(f"SELECT * FROM {old_table}").fetchall()
    pos = _positions(columns)
    for row in rows:
        old_id = read_column(row, pos, "id")
        new_id = str(uuid.uuid4())
        id_map[old_id] = new_id
        values: list[Any] = []
        for name in new_names:
            if name == "id":
                values.append(new_id)
                continue
            if name in ("timestamp", "created_at"):
                # The canonical column absorbs the legacy ``timestamp`` value; a
                # row that never set one gets a fresh ISO-8601 stamp.
                values.append(_to_iso(read_column(row, pos, "timestamp")))
                continue
            if name == "tags":
                raw = read_column(row, pos, "tags")
                values.append(raw if raw else "[]")
                continue
            if name == "status":
                raw = read_column(row, pos, "status")
                # Legacy rows used 'pending'; the canonical default is 'proposed'.
                values.append(raw if raw else "proposed")
                continue
            if name in refs:
                raw = read_column(row, pos, name)
                parent = _parent_for(name)
                values.append(parent_maps.get(parent, {}).get(raw, str(raw or "")))
                continue
            raw = read_column(row, pos, name)
            values.append(raw if raw is not None else "")
        placeholders = ", ".join("?" for _ in new_names)
        columns_sql = ", ".join(new_names)
        conn.execute(
            f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})",
            tuple(values),
        )

    conn.execute(f"DROP TABLE {old_table}")
    for sql in indexes:
        conn.execute(sql)
    parent_maps[table] = id_map


def _parent_for(ref_column: str) -> str:
    return {
        "observation_id": "observations",
        "hypothesis_id": "hypotheses",
        "test_id": "research_tests",
        "result_id": "research_results",
    }[ref_column]


def _new_column_names(ddl: str) -> list[str]:
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]
    names = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK")):
            continue
        names.append(line)
    return names


def _to_iso(raw: Any) -> str:
    if raw is None or raw == "":
        return _now_iso()
    text = str(raw)
    if "T" in text:
        return text
    # sqlite CURRENT_TIMESTAMP renders as 'YYYY-MM-DD HH:MM:SS' in UTC.
    return text.replace(" ", "T") + "+00:00"


def _sqlite_up(conn: Any) -> None:
    parent_maps: dict[str, dict[Any, str]] = {}
    for table, ddl, refs in _SPEC:
        if not _sqlite_needs_rebuild(conn, table):
            continue
        _sqlite_rebuild(conn, table, ddl, refs, parent_maps)


def _postgres_up(conn: Any) -> None:
    """ALTER in place: PostgreSQL can retype a column without rebuilding the table."""
    for table, _ddl, refs in _SPEC:
        columns = {
            name: data_type
            for name, data_type in execute(
                conn,
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = %s",
                (table,),
            ).fetchall()
        }
        if not columns:
            continue

        if columns.get("id") in ("integer", "bigint", "smallint"):
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN id TYPE TEXT USING id::text")
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
        for ref in refs:
            if columns.get(ref) in ("integer", "bigint", "smallint"):
                execute(
                    conn,
                    f"ALTER TABLE {table} ALTER COLUMN {ref} TYPE TEXT USING {ref}::text",
                )
        if columns.get("timestamp") in ("timestamp without time zone", "timestamp with time zone"):
            execute(
                conn,
                f"ALTER TABLE {table} ALTER COLUMN timestamp TYPE TEXT "
                f'USING to_char({table}.timestamp, \'YYYY-MM-DD"T"HH24:MI:SS"+00:00"\')',
            )
        # The canonical shape names this column created_at on hypotheses and
        # lessons; leaving it as timestamp would leave PostgreSQL disagreeing
        # with the repositories and with the SQLite path.
        if "created_at" not in columns and "timestamp" in columns and table != "observations":
            execute(conn, f"ALTER TABLE {table} RENAME COLUMN timestamp TO created_at")
        # The legacy DEFAULT CURRENT_TIMESTAMP is exactly the bug: a TIMESTAMP-typed
        # default surviving on a now-TEXT column, which hides a writer that forgot to
        # supply the value. The repositories always supply the timestamp, so the
        # default is dropped and the column is required -- fail closed rather than
        # silently stamp the wrong shape. ``stamp`` is the post-rename name, because
        # ``columns`` was read before the rename above.
        if "created_at" not in columns and "timestamp" in columns and table != "observations":
            stamp = "created_at"
        else:
            stamp = "timestamp"
        if stamp in ("timestamp", "created_at") and (stamp in columns or stamp == "created_at"):
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN {stamp} DROP DEFAULT")
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN {stamp} SET NOT NULL")
        for required in ("content", "symbol", *refs):
            if columns.get(required) is not None:
                execute(conn, f"ALTER TABLE {table} ALTER COLUMN {required} SET NOT NULL")
        if columns.get("status") is not None:
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN status SET DEFAULT 'proposed'")
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN status SET NOT NULL")
        if columns.get("tags") is not None:
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN tags SET DEFAULT '[]'")
            execute(conn, f"ALTER TABLE {table} ALTER COLUMN tags SET NOT NULL")


def up(conn: Any, backend: str = "sqlite") -> None:
    backend = backend or detect_backend(conn)
    if backend == PG:
        _postgres_up(conn)
    else:
        _sqlite_up(conn)


def down(conn: Any, backend: str = "sqlite") -> None:
    """Not reversible: the legacy integer-keyed shape is being abandoned.

    Returning to INTEGER keys would require inventing identities for rows that
    only ever had TEXT uuids, and would reintroduce the DatatypeMismatch against
    the repositories. Refusing to pretend otherwise is safer than a lossy revert.
    """
    return
