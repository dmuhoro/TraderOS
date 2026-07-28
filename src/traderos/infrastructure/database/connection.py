from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from traderos.infrastructure.config.config_loader import Config


def resolve_backend(database_url: str = "") -> str:
    url = database_url or os.getenv("DATABASE_URL", "")
    if url.startswith(("postgresql://", "postgres://")):
        return "postgres"
    return "sqlite"


def get_connection(config: Config | None = None) -> Any:
    cfg = config or Config.load()
    url = cfg.database_url or os.getenv("DATABASE_URL", "")
    if url.startswith(("postgresql://", "postgres://")):
        return _connect_postgres(url)
    return _connect_sqlite(cfg)


def _connect_postgres(database_url: str) -> Any:
    try:
        import psycopg2
    except ImportError as err:
        raise ImportError(
            "psycopg2-binary is required for PostgreSQL. "
            "Install it with: pip install traderos[postgres]"
        ) from err
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return conn


def _connect_sqlite(config: Config) -> sqlite3.Connection:
    db_path = os.getenv("DB_PATH") or config.db_path
    if db_path == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def close_connection(conn: Any) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Error closing database connection")
