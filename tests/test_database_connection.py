from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.infrastructure.database.connection import ConnectionPool
from traderos.infrastructure.database.connection import close_all_pools
from traderos.infrastructure.database.connection import resolve_backend


class TestResolveBackend:
    def test_sqlite_default(self):
        assert resolve_backend("") == "sqlite"
        assert resolve_backend("sqlite:///foo.db") == "sqlite"

    def test_postgres_urls(self):
        assert resolve_backend("postgresql://user:pass@host/db") == "postgres"
        assert resolve_backend("postgres://user:pass@host/db") == "postgres"

    def test_postgres_raises_without_psycopg2(self):
        import sys

        if "psycopg2" in sys.modules:
            pass
        else:
            from traderos.infrastructure.database.connection import _connect_postgres

            try:
                _connect_postgres("postgresql://localhost/test")
                raise AssertionError("should have raised ImportError")
            except ImportError:
                pass


class TestConnectionPool:
    def test_acquire_release(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value.execute.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch.object(ConnectionPool, "_create_connection", return_value=mock_conn):
            pool = ConnectionPool(dsn="postgresql://localhost/test", min_connections=0)
            conn = pool.acquire()
            assert conn is mock_conn
            assert len(pool._in_use) == 1
            pool.release(conn)
            assert len(pool._in_use) == 0
            assert len(pool._pool) == 1

    def test_pool_exhaustion_raises(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value.execute.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch.object(ConnectionPool, "_create_connection", return_value=mock_conn):
            pool = ConnectionPool(
                dsn="postgresql://localhost/test", min_connections=0, max_connections=1
            )
            pool.acquire()
            with pytest.raises(RuntimeError, match="Connection pool exhausted"):
                pool.acquire()

    def test_unhealthy_connection_replaced(self):
        mock_good = MagicMock()
        mock_bad = MagicMock()
        mock_good_cursor = MagicMock()
        mock_good_cursor.__enter__.return_value.execute.return_value = None
        mock_good.cursor.return_value.__enter__.return_value = mock_good_cursor
        mock_bad.cursor.side_effect = Exception("Connection lost")

        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = pool._lock if hasattr(pool, "_lock") else __import__("threading").Lock()
        pool._lock = __import__("threading").Lock()
        pool._pool = []
        pool._in_use = set()
        pool._closed = False

        pool._pool.append(mock_bad)
        with patch.object(pool, "_create_connection", return_value=mock_good):
            conn = pool.acquire()
            assert conn is mock_good
            assert mock_bad.close.called

    def test_close_all(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value.execute.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch.object(ConnectionPool, "_create_connection", return_value=mock_conn):
            pool = ConnectionPool(dsn="postgresql://localhost/test", min_connections=1)
            pool.acquire()
            pool.close_all()
            assert pool._closed is True
            assert len(pool._pool) == 0
            assert len(pool._in_use) == 0

    def test_close_all_pools_global(self):
        close_all_pools()

    def test_pool_stats(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value.execute.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch.object(ConnectionPool, "_create_connection", return_value=mock_conn):
            pool = ConnectionPool(dsn="postgresql://localhost/test", min_connections=2)
            stats = pool.stats
            assert stats["min"] == 2
            assert stats["max"] == 10
            assert stats["available"] == 2
            assert stats["in_use"] == 0
