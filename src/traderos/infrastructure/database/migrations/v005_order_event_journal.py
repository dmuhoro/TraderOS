from __future__ import annotations

from typing import Any

from traderos.infrastructure.database.migration_utils import execute

PG = "postgres"

VERSION = 5
DESCRIPTION = "Durable order-event journal: idempotency keys, outbox state, replay"

TABLE = "order_events"


# Tables created by this migration. The version marker is a claim, not a proof:
# migration_manager.schema_drift() checks these actually exist so a marker at head
# can never silently paper over a missing table.
TABLES: tuple[str, ...] = ("order_events",)


def up(conn: Any, backend: str = "sqlite") -> None:
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id TEXT PRIMARY KEY,
            trade_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{{}}',
            published INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT NOT NULL
        )
    """,
    )
    execute(conn, f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_trade_id ON {TABLE}(trade_id)")
    execute(conn, f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_published ON {TABLE}(published)")
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    execute(conn, f"DROP TABLE IF EXISTS {TABLE}")
    conn.commit()
