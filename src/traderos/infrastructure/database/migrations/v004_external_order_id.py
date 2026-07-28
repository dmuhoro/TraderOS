from __future__ import annotations

from typing import Any

PG = "postgres"

VERSION = 4
DESCRIPTION = "Add external_order_id column to trades table"


def up(conn: Any, backend: str = "sqlite") -> None:
    if backend == PG:
        conn.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS external_order_id TEXT")
    else:
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN external_order_id TEXT")
        except Exception:
            pass
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    if backend == PG:
        conn.execute("ALTER TABLE trades DROP COLUMN IF EXISTS external_order_id")
    else:
        conn.execute("ALTER TABLE trades DROP COLUMN external_order_id")
    conn.commit()
