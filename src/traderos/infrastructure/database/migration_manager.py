import importlib
import os
from typing import Any

from traderos.infrastructure.database.migration_utils import PG
from traderos.infrastructure.database.migration_utils import detect_backend
from traderos.infrastructure.database.migration_utils import execute

SCHEMA_VERSION_TABLE = "_schema_version"


def _ensure_version_table(conn: Any):
    backend = detect_backend(conn)
    if backend == PG:
        execute(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                description TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        )
    else:
        execute(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                description TEXT,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """,
        )
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
                "tables": tuple(getattr(mod, "TABLES", ()) or ()),
            }
        )
    return migrations


def get_current_version(conn: Any) -> int:
    _ensure_version_table(conn)
    row = execute(conn, f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}").fetchone()
    return row[0] if row else 0


class SchemaDriftError(RuntimeError):
    """The version marker claims a migration ran, but its tables are absent.

    The marker is a claim, never a proof. A test that drops a table, a failed
    up() that committed its marker before finishing, or a restored dump can all
    leave ``_schema_version`` at head while the schema is incomplete. migrate()
    then no-ops and the application starts against tables that do not exist.
    """

    def __init__(self, drift: dict[int, tuple[str, ...]]) -> None:
        self.drift = drift
        detail = "; ".join(f"v{v}: missing {', '.join(t)}" for v, t in sorted(drift.items()))
        super().__init__(f"schema marker at head but schema incomplete -> {detail}")


def _existing_tables(conn: Any) -> set[str]:
    backend = detect_backend(conn)
    if backend == PG:
        rows = execute(
            conn,
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()",
        ).fetchall()
    else:
        rows = execute(conn, "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def _drift_for(migrations: list[dict[str, Any]], current: int, existing: set[str]) -> dict:
    """Per applied version, the tables its migration declared but that are gone."""
    drift: dict[int, tuple[str, ...]] = {}
    for m in migrations:
        version = int(m["version"])
        if version > current:
            continue
        missing = tuple(t for t in m["tables"] if t not in existing)
        if missing:
            drift[version] = missing
    return drift


def schema_drift(conn: Any, migrations_dir: str | None = None) -> dict[int, tuple[str, ...]]:
    """Report marker/schema disagreement for every migration already marked applied."""
    current = get_current_version(conn)
    if current <= 0:
        return {}
    return _drift_for(_discover_migrations(migrations_dir), current, _existing_tables(conn))


def latest_version(migrations_dir: str | None = None) -> int:
    """The head version of the migration chain, without touching a database.

    Evidence drills and schema assertions need to know the expected head so they
    can fail when a database is behind. That is a legitimate query about the
    chain, so it is public here rather than reached for by importing the private
    `_discover_migrations`. Deriving it from the chain is the whole point: a
    hardcoded literal silently rots each time a migration is added, which is how
    a drill ends up asserting a version nobody ships.
    """
    migrations = _discover_migrations(migrations_dir)
    return max((int(m["version"]) for m in migrations), default=0)


def _version_placeholder(backend: str) -> str:
    return "%s" if backend == PG else "?"


def migrate(
    conn: Any,
    target_version: int | None = None,
    migrations_dir: str | None = None,
    repair: bool = True,
):
    _ensure_version_table(conn)
    migrations = _discover_migrations(migrations_dir)
    backend = detect_backend(conn)
    ph = _version_placeholder(backend)

    if target_version is None:
        target_version = max(m["version"] for m in migrations) if migrations else 0

    current = get_current_version(conn)
    if not isinstance(target_version, int):
        raise TypeError(f"target_version must be int, got {type(target_version).__name__}")

    drift = _drift_for(migrations, current, _existing_tables(conn))
    if drift:
        if not repair:
            raise SchemaDriftError(drift)
        by_version = {int(m["version"]): m for m in migrations}
        for version in sorted(drift):
            # Every up() in the chain is CREATE TABLE IF EXISTS, so re-running
            # one recreates only what is missing and cannot touch existing rows.
            by_version[version]["up"](conn, backend=backend)
            conn.commit()

    if target_version > current:
        pending = [
            m for m in migrations if m["version"] > current and m["version"] <= target_version
        ]
        for m in pending:
            m["up"](conn, backend=backend)
            execute(
                conn,
                f"INSERT INTO {SCHEMA_VERSION_TABLE} (version, description) VALUES ({ph}, {ph})",
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
            # Remove the version marker BEFORE applying the down() so a
            # partially-failed down can never leave a phantom version row
            # pointing at dropped tables (OT-005). All down() implementations
            # are idempotent (DROP TABLE IF EXISTS), so retries are safe.
            execute(
                conn,
                f"DELETE FROM {SCHEMA_VERSION_TABLE} WHERE version = {ph}",
                (m["version"],),
            )
            conn.commit()
            m["down"](conn, backend=backend)
            conn.commit()
