from __future__ import annotations

from typing import Any

from traderos.infrastructure.database.migration_utils import execute

PG = "postgres"

VERSION = 4
DESCRIPTION = "Add external_order_id column to trades table"


def _trades_table_exists(conn: Any, backend: str) -> bool:
    if backend == PG:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", ("trades",))
        return cur.fetchone()[0] is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trades'"
    ).fetchone()
    return row is not None


def up(conn: Any, backend: str = "sqlite") -> None:
    # The trades table is created by the repository layer (CREATE TABLE IF
    # NOT EXISTS) with external_order_id already present. On a fresh schema
    # the table may not exist yet, so the ALTER must be guarded (H7).
    if not _trades_table_exists(conn, backend):
        conn.commit()
        return
    if backend == PG:
        execute(conn, "ALTER TABLE trades ADD COLUMN IF NOT EXISTS external_order_id TEXT")
    else:
        try:
            execute(conn, "ALTER TABLE trades ADD COLUMN external_order_id TEXT")
        except Exception:
            pass
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    if not _trades_table_exists(conn, backend):
        conn.commit()
        return
    if backend == PG:
        execute(conn, "ALTER TABLE trades DROP COLUMN IF EXISTS external_order_id")
    else:
        columns = conn.execute("PRAGMA table_info(trades)").fetchall()
        has_column = any(col[1] == "external_order_id" for col in columns)
        if has_column:
            execute(conn, "ALTER TABLE trades DROP COLUMN external_order_id")
    conn.commit()
