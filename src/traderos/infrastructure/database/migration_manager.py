import importlib
import os
from typing import Any

SCHEMA_VERSION_TABLE = "_schema_version"


def _ensure_version_table(conn: Any):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
            version INTEGER PRIMARY KEY,
            description TEXT,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _discover_migrations(migrations_dir: str | None = None) -> list[dict[str, Any]]:
    if migrations_dir is None:
        migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")

    migrations = []
    for fname in sorted(os.listdir(migrations_dir)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        mod_name = fname[:-3]
        mod_path = f"traderos.infrastructure.database.migrations.{mod_name}"
        mod = importlib.import_module(mod_path)
        migrations.append(
            {
                "version": mod.VERSION,
                "description": mod.DESCRIPTION,
                "up": mod.up,
                "down": mod.down,
            }
        )
    return migrations


def get_current_version(conn: Any) -> int:
    _ensure_version_table(conn)
    row = conn.execute(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}").fetchone()
    return row[0] if row else 0


def migrate(conn: Any, target_version: int | None = None, migrations_dir: str | None = None):
    _ensure_version_table(conn)
    migrations = _discover_migrations(migrations_dir)

    if target_version is None:
        target_version = max(m["version"] for m in migrations) if migrations else 0

    current = get_current_version(conn)
    assert isinstance(target_version, int)

    if target_version > current:
        pending = [
            m for m in migrations if m["version"] > current and m["version"] <= target_version
        ]
        for m in pending:
            m["up"](conn)
            conn.execute(
                f"INSERT INTO {SCHEMA_VERSION_TABLE} (version, description) VALUES (?, ?)",
                (m["version"], m["description"]),
            )
            conn.commit()

    elif target_version < current:
        pending = [
            m
            for m in reversed(migrations)
            if m["version"] <= current and m["version"] > target_version
        ]
        for m in pending:
            m["down"](conn)
            conn.execute(f"DELETE FROM {SCHEMA_VERSION_TABLE} WHERE version = ?", (m["version"],))
            conn.commit()
