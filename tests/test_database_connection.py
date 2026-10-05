from __future__ import annotations

import sqlite3
import threading
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database import connection as connection_module
from traderos.infrastructure.database.connection import ConnectionPool
from traderos.infrastructure.database.connection import _connect_postgres
from traderos.infrastructure.database.connection import _connect_sqlite
from traderos.infrastructure.database.connection import close_all_pools
from traderos.infrastructure.database.connection import close_connection
from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.database.connection import pooled_connection
from traderos.infrastructure.database.connection import resolve_backend


class TestResolveBackend:
    def test_sqlite_default(self):
        assert resolve_backend("") == "sqlite"
        assert resolve_backend("sqlite:///foo.db") == "sqlite"

    def test_postgres_urls(self):
        assert resolve_backend("postgresql://user:pass@host/db") == "postgres"
        assert resolve_backend("postgres://user:pass@host/db") == "postgres"

    def test_postgres_raises_without_psycopg2(self):
        with (
            patch.dict("sys.modules", {"psycopg2": None}),
            pytest.raises(ImportError, match="psycopg2"),
        ):
            _connect_postgres("postgresql://localhost/test")

    def test_default_config_refuses_the_ambient_database_url(self, monkeypatch):
        """``Config()`` is an explicit "not PostgreSQL" and must stay one.

        ``database_url`` is typed ``str | None`` because ``Config.load()`` assigns
        ``os.getenv("DATABASE_URL")`` and yields None when it is unset. The
        *default* must still be ``""``: with None, every bare ``Config()`` asked
        the environment which backend to use and adopted whatever DATABASE_URL
        happened to hold. 21 tests silently moved onto PostgreSQL that way, and on
        a host with no pg_dump the backup paths then failed. A default that
        inherits the environment is fail-open, and this is the pin for it.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://someone:pw@elsewhere:5432/db")

        assert Config().database_url == "", "the default must not consult the environment"
        assert resolve_backend(Config().database_url) == "sqlite"

        # None is the one value that does consult the environment: it is what
        # Config.load() produces when DATABASE_URL is genuinely unset, and
        # resolve_backend's documented contract is that None means "not
        # specified". The fail-closed guarantee comes from Config()'s default,
        # not from resolve_backend overriding its own contract.
        assert resolve_backend(None) == "postgres"


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


class TestGetConnection:
    def test_get_connection_sqlite(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "test.db")))
        assert conn._backend == "sqlite"
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

    def test_get_connection_in_memory_sqlite(self):
        conn = _connect_sqlite(Config(db_path=":memory:"))
        assert conn._backend == "sqlite"
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()

    def test_get_connection_postgres_delegates(self):
        mock_conn = MagicMock()
        with patch.object(connection_module, "_connect_postgres", return_value=mock_conn) as m:
            conn = get_connection(Config(database_url="postgresql://user:pass@host/db"))
        assert conn is mock_conn
        m.assert_called_once_with("postgresql://user:pass@host/db")


class TestConnectPostgres:
    def test_success(self):
        """A loopback DSN reaches the driver and gets autocommit disabled.

        The URL is loopback because ``_connect_postgres`` is now interlocked:
        under pytest it refuses a non-loopback host before the driver is even
        imported. Using a bare "host" here made this test assert the opposite of
        the guard's purpose, and it was silently red the moment the guard landed.
        The refusal itself is asserted in ``test_remote_url_is_refused`` below and
        in tests/wiring/test_remote_destructive_guard.py.
        """
        fake = ModuleType("psycopg2")
        fake.connect = MagicMock()
        conn = MagicMock()
        fake.connect.return_value = conn
        with patch.dict("sys.modules", {"psycopg2": fake}):
            result = _connect_postgres("postgresql://user:pass@localhost/db")
        assert result is conn
        fake.connect.assert_called_once_with("postgresql://user:pass@localhost/db")
        assert conn.autocommit is False

    def test_remote_url_is_refused(self):
        """The interlock, asserted at the function every real caller funnels into."""
        from traderos.infrastructure.database.safety_guard import RemoteDestructiveRefusedError

        fake = ModuleType("psycopg2")
        fake.connect = MagicMock()
        with (
            patch.dict("sys.modules", {"psycopg2": fake}),
            pytest.raises(RemoteDestructiveRefusedError),
        ):
            _connect_postgres("postgresql://user:pass@db.example.invalid/db")
        fake.connect.assert_not_called()

    def test_missing_psycopg2_raises(self):
        with (
            patch.dict("sys.modules", {"psycopg2": None}),
            pytest.raises(ImportError, match="psycopg2"),
        ):
            _connect_postgres("postgresql://localhost/test")


class TestSqliteConnectionMethods:
    def test_cursor_executemany_and_fetchmany(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "t.db")))
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        cur = conn.execute("INSERT INTO t VALUES (1, 'a')")
        cur.executemany("INSERT INTO t VALUES (?, ?)", [(2, "b"), (3, "c")])
        rows = conn.execute("SELECT * FROM t ORDER BY id").fetchmany(2)
        assert len(rows) == 2
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(4, "d"), (5, "e")])
        conn.commit()
        assert conn.total_changes >= 4
        conn.close()

    def test_executescript(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "e.db")))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.executescript("INSERT INTO t VALUES (1); INSERT INTO t VALUES (2);")
        rows = conn.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 2
        conn.close()

    def test_rollback(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "rb.db")))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (99)")
        conn.rollback()
        rows = conn.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_context_manager_returns_self_and_closes(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "cm.db")))
        with conn as c:
            assert c is conn
            c.execute("CREATE TABLE t (id INTEGER)")
        with pytest.raises(sqlite3.ProgrammingError):
            conn._conn.execute("SELECT 1")

    def test_getattr_delegates(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "ga.db")))
        assert conn.total_changes == 0
        conn.close()

    def test_cursor_is_iterable_like_a_real_sqlite_cursor(self, tmp_path):
        """``for row in conn.execute(...)`` must work through the proxy.

        The proxy's ``__getattr__`` cannot supply ``__iter__``: dunder lookups go
        through the type, not the instance, so the cursor wrapper silently
        stopped being a drop-in ``sqlite3.Cursor``. Every caller of that idiom
        -- the migration runners (``v010``/``v012`` read ``PRAGMA table_info``
        this way), ``run_manifest``, and the sqlite repositories -- raised
        ``TypeError: '_ThreadSafeSQLiteCursor' object is not iterable``.

        Asserted against the values a real query returns, because "did not
        raise" alone would also pass for an iterator that yields nothing.
        """
        conn = get_connection(Config(db_path=str(tmp_path / "iter.db")))
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b"), (3, "c")])

        rows = list(conn.execute("SELECT id, name FROM t ORDER BY id"))
        assert [(r["id"], r["name"]) for r in rows] == [(1, "a"), (2, "b"), (3, "c")]

        # The migration runners' idiom: index access by column position.
        assert {r[1] for r in conn.execute("PRAGMA table_info(t)")} == {"id", "name"}

        # Iteration must work on a cursor() handle too, not just execute().
        assert len(list(conn.cursor().execute("SELECT name FROM t"))) == 3
        conn.close()

    def test_iteration_is_safe_while_another_thread_writes(self, tmp_path):
        """Rows are materialised under the connection lock, not streamed.

        A real ``sqlite3.Cursor`` yields lazily, so a proxy that delegated
        ``__iter__`` to the live cursor would hand callers rows outside the lock
        this class exists to hold. A concurrent writer must not corrupt or lose
        the rows the reader already committed to iterating.
        """
        conn = get_connection(Config(db_path=str(tmp_path / "race.db")))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
        conn.commit()

        started = threading.Event()
        done = threading.Event()

        def _writer() -> None:
            started.wait(timeout=5)
            for i in range(50, 100):
                conn.execute("INSERT INTO t VALUES (?)", (i,))
            conn.commit()
            done.set()

        writer = threading.Thread(target=_writer)
        writer.start()
        started.set()
        rows = [r[0] for r in conn.execute("SELECT id FROM t WHERE id < 50 ORDER BY id")]
        writer.join(timeout=10)
        done.wait(timeout=5)

        assert rows == list(range(50))
        conn.close()


class TestCloseConnection:
    def test_none_is_noop(self):
        close_connection(None)

    def test_survives_close_error(self):
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("boom")
        with patch("logging.getLogger"):
            close_connection(mock_conn)


class TestConnectionPoolExtras:
    def test_create_connection_delegates(self):
        mock_conn = MagicMock()
        with patch.object(connection_module, "_connect_postgres", return_value=mock_conn) as m:
            pool = ConnectionPool.__new__(ConnectionPool)
            pool._dsn = "postgresql://localhost/test"
            assert pool._create_connection() is mock_conn
        m.assert_called_once_with("postgresql://localhost/test")

    def test_is_healthy_false_on_error(self):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = RuntimeError("down")
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        assert pool._is_healthy(mock_conn) is False

    def test_acquire_closes_unhealthy_even_if_close_fails(self):
        mock_bad = MagicMock()
        mock_bad.cursor.side_effect = Exception("down")
        mock_bad.close.side_effect = Exception("close fail")
        mock_good = MagicMock()
        mock_good.cursor.return_value.__enter__.return_value.execute.return_value = None

        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = [mock_bad]
        pool._in_use = set()
        pool._closed = False
        with patch.object(pool, "_create_connection", return_value=mock_good):
            conn = pool.acquire()
        assert conn is mock_good
        assert mock_bad.close.called

    def test_release_closed_pool_closes_conn(self):
        mock_conn = MagicMock()
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = []
        pool._in_use = {mock_conn}
        pool._closed = True
        pool.release(mock_conn)
        assert mock_conn.close.called
        assert mock_conn not in pool._in_use

    def test_release_unhealthy_closes_conn(self):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("down")
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = []
        pool._in_use = {mock_conn}
        pool._closed = False
        pool.release(mock_conn)
        assert mock_conn.close.called
        assert pool._pool == []

    def test_close_all_closes_pooled_connections(self):
        mock_conn = MagicMock()
        with patch.object(ConnectionPool, "_create_connection", return_value=mock_conn):
            pool = ConnectionPool(dsn="postgresql://localhost/test", min_connections=1)
        pool.close_all()
        assert mock_conn.close.called
        assert pool._closed is True

    def test_cursor_context_manager_and_fetch(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "c.db")))
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")])
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM t ORDER BY id")
            row = cur.fetchone()
            assert row[0] == 1
            rows = cur.fetchmany(1)
            assert len(rows) == 1
            assert cur.rowcount == -1
        conn.close()

    def test_row_factory_getter_setter(self, tmp_path):
        conn = get_connection(Config(db_path=str(tmp_path / "rf.db")))
        assert conn.row_factory is sqlite3.Row
        conn.row_factory = None
        conn.close()

    def test_acquire_closed_pool_raises(self):
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = []
        pool._in_use = set()
        pool._closed = True
        with pytest.raises(RuntimeError, match="closed"):
            pool.acquire()

    def test_release_closed_pool_survives_close_error(self):
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("nope")
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = []
        pool._in_use = {mock_conn}
        pool._closed = True
        pool.release(mock_conn)

    def test_release_unhealthy_survives_close_error(self):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("down")
        mock_conn.close.side_effect = Exception("nope")
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = []
        pool._in_use = {mock_conn}
        pool._closed = False
        pool.release(mock_conn)

    def test_close_all_survives_close_errors(self):
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("nope")
        pool = ConnectionPool.__new__(ConnectionPool)
        pool._dsn = ""
        pool._min = 0
        pool._max = 10
        pool._timeout = 30
        pool._lock = threading.Lock()
        pool._pool = [mock_conn]
        pool._in_use = {mock_conn}
        pool._closed = False
        pool.close_all()
        assert pool._closed is True


class TestPooledConnection:
    def test_success_commits(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value.execute.return_value = None
        dsn = "postgresql://localhost/pooled-ok"
        with (
            patch.object(ConnectionPool, "_create_connection", return_value=mock_conn),
            pooled_connection(dsn) as conn,
        ):
            assert conn is mock_conn
        assert mock_conn.commit.called
        close_all_pools()

    def test_error_rolls_back_and_reraises(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value.execute.return_value = None
        dsn = "postgresql://localhost/pooled-bad"
        with (
            patch.object(ConnectionPool, "_create_connection", return_value=mock_conn),
            pytest.raises(ValueError, match="boom"),
            pooled_connection(dsn),
        ):
            raise ValueError("boom")
        assert mock_conn.rollback.called
        close_all_pools()

    def test_close_all_pools_iterates(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value.execute.return_value = None
        dsn = "postgresql://localhost/pooled-cleanup"
        with (
            patch.object(ConnectionPool, "_create_connection", return_value=mock_conn),
            pooled_connection(dsn),
        ):
            pass
        close_all_pools()
