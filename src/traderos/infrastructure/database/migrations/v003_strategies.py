from __future__ import annotations

from typing import Any

from traderos.infrastructure.database.migration_utils import execute

PG = "postgres"

VERSION = 3
DESCRIPTION = "Strategy registry table with 3 built-in seed strategies"


def up(conn: Any, backend: str = "sqlite") -> None:
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS strategy_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            params TEXT NOT NULL DEFAULT '{}',
            version TEXT NOT NULL DEFAULT '1.0.0',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    )
    if backend == PG:
        execute(
            conn,
            """
            INSERT INTO strategy_registry (id, name, params, version, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """,
            ("moving_average_trend", "moving_average_trend", "{}", "1.0.0", "active"),
        )
        execute(
            conn,
            """
            INSERT INTO strategy_registry (id, name, params, version, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """,
            ("volatility_breakout", "volatility_breakout", "{}", "1.0.0", "active"),
        )
        execute(
            conn,
            """
            INSERT INTO strategy_registry (id, name, params, version, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """,
            ("mean_reversion", "mean_reversion", "{}", "1.0.0", "active"),
        )
    else:
        execute(
            conn,
            """
            INSERT OR IGNORE INTO strategy_registry (id, name, params, version, status)
            VALUES (?, ?, ?, ?, ?)
        """,
            ("moving_average_trend", "moving_average_trend", "{}", "1.0.0", "active"),
        )
        execute(
            conn,
            """
            INSERT OR IGNORE INTO strategy_registry (id, name, params, version, status)
            VALUES (?, ?, ?, ?, ?)
        """,
            ("volatility_breakout", "volatility_breakout", "{}", "1.0.0", "active"),
        )
        execute(
            conn,
            """
            INSERT OR IGNORE INTO strategy_registry (id, name, params, version, status)
            VALUES (?, ?, ?, ?, ?)
        """,
            ("mean_reversion", "mean_reversion", "{}", "1.0.0", "active"),
        )
    conn.commit()


def down(conn: Any, backend: str = "sqlite") -> None:
    execute(conn, "DROP TABLE IF EXISTS strategy_registry")
    conn.commit()
