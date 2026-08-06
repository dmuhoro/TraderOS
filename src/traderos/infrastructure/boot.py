"""Deployment boot actions: migrations-on-boot (A4).

A4 requires that a deployed instance applies pending schema migrations on boot
and **fails closed** if the database cannot be migrated — never serving against
a stale or partially-migrated store. When ``DATABASE_URL`` is absent we default
to the local SQLite path; if ``RUN_MIGRATIONS_ON_BOOT=false`` (or the URL is
explicitly empty) the step is skipped so tests / operators can opt out.
"""

from __future__ import annotations

import logging
import os

from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database.connection import close_connection
from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.database.connection import resolve_backend
from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import migrate

logger = logging.getLogger("traderos.boot")


class MigrationsOnBootError(RuntimeError):
    """Raised when the on-boot migration could not complete and the process
    must refuse to start (fail closed, never serve on a bad schema)."""


def run_migrations_on_boot(config: Config | None = None) -> int | None:
    """Apply pending migrations before serving.

    Returns the resulting schema version, or ``None`` when migrations are
    disabled. Raises ``MigrationsOnBootError`` if a required migration fails —
    the caller must refuse to boot.
    """
    if os.getenv("RUN_MIGRATIONS_ON_BOOT", "true").lower() in ("0", "false", "no", "off"):
        logger.info("migrations-on-boot disabled via RUN_MIGRATIONS_ON_BOOT")
        return None

    cfg = config or Config.load()
    url = cfg.database_url or os.getenv("DATABASE_URL", "")
    if not url:
        logger.info("no DATABASE_URL configured; on-boot migrations skipped (dev default SQLite)")
        return None
    # A managed deployment (A4/A5) always runs on a DATABASE_URL. On that
    # backend the stored schema must be migratable or we refuse to serve.
    conn = None
    try:
        conn = get_connection(cfg)
        migrate(conn)
        version = get_current_version(conn)
        conn.commit()
        backend = resolve_backend(url)
        logger.info("migrations applied on boot: backend=%s schema_version=%s", backend, version)
        return version
    except Exception as exc:
        raise MigrationsOnBootError(
            f"migrations-on-boot FAILED: {exc} — refusing to serve on a possibly stale schema"
        ) from exc
    finally:
        if conn is not None:
            close_connection(conn)


def require_backend(sqlite_ok: bool = True) -> str:
    """Resolve the configured backend, asserting it is deployable."""
    cfg = Config.load()
    url = cfg.database_url or os.getenv("DATABASE_URL", "")
    backend = resolve_backend(url)
    if backend == "sqlite" and not sqlite_ok:
        raise MigrationsOnBootError(
            "SQLite backend is not allowed in this deployment; set DATABASE_URL to a "
            "PostgreSQL DSN so migrations-on-boot applies to a durable store"
        )
    return backend
