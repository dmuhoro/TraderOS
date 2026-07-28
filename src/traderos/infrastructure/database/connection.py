from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from traderos.infrastructure.config.config_loader import Config

POOL_SIZE_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_SIZE_MAX = int(os.getenv("DB_POOL_MAX", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


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


class ConnectionPool:
    def __init__(
        self,
        dsn: str = "",
        min_connections: int = POOL_SIZE_MIN,
        max_connections: int = POOL_SIZE_MAX,
        timeout: int = POOL_TIMEOUT,
    ) -> None:
        self._dsn = dsn
        self._min = min_connections
        self._max = max_connections
        self._timeout = timeout
        self._lock = threading.Lock()
        self._pool: list[Any] = []
        self._in_use: set[Any] = set()
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        for _ in range(self._min):
            conn = self._create_connection()
            self._pool.append(conn)

    def _create_connection(self) -> Any:
        return _connect_postgres(self._dsn)

    def _is_healthy(self, conn: Any) -> bool:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def acquire(self) -> Any:
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        with self._lock:
            while self._pool:
                conn = self._pool.pop()
                if self._is_healthy(conn):
                    self._in_use.add(conn)
                    return conn
                try:
                    conn.close()
                except Exception:
                    pass
            if len(self._in_use) < self._max:
                conn = self._create_connection()
                self._in_use.add(conn)
                return conn
        raise RuntimeError(
            f"Connection pool exhausted (max={self._max}, in_use={len(self._in_use)})"
        )

    def release(self, conn: Any) -> None:
        with self._lock:
            self._in_use.discard(conn)
            if self._closed:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            if self._is_healthy(conn):
                self._pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()
            for conn in list(self._in_use):
                try:
                    conn.close()
                except Exception:
                    pass
            self._in_use.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "pool_size": len(self._pool),
                "in_use": len(self._in_use),
                "available": len(self._pool),
                "max": self._max,
                "min": self._min,
            }


@contextmanager
def pooled_connection(dsn: str = "") -> Generator[Any, None, None]:
    global _POOLS
    with _POOLS_LOCK:
        if dsn not in _POOLS:
            _POOLS[dsn] = ConnectionPool(dsn=dsn)
        pool = _POOLS[dsn]
    conn = pool.acquire()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.release(conn)


def close_all_pools() -> None:
    global _POOLS
    with _POOLS_LOCK:
        for pool in _POOLS.values():
            pool.close_all()
        _POOLS.clear()
