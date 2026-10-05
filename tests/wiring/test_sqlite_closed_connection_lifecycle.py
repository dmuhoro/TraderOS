"""Proof for the SQLite use-after-close crash (D-2).

A full-suite run aborted with::

    INTERNALERROR> sqlite3.ProgrammingError: Cannot operate on a closed database.

The statement was ``INSERT INTO health_history`` issued from a background
worker that outlived the test that owned the connection. These tests pin the
three behaviours that make that failure deterministic and non-fatal:

1. Use-after-close raises ``ConnectionClosedError`` (a distinct type), not the
   opaque ``sqlite3.ProgrammingError`` that is indistinguishable from bad SQL.
2. ``close()`` is idempotent.
3. A late health-history write on a closed connection is skipped, counted and
   logged -- the in-memory status is still returned to the caller.

They use the real connection factory and the real health service. No mocks:
the crash only appears when the real wrapper and the real writer are wired
together exactly as production wires them.

Every test forces the sqlite branch explicitly. ``get_connection`` resolves the
backend as ``cfg.database_url or os.getenv("DATABASE_URL")`` and ``load_dotenv``
pulls a production Supabase URL into the environment at import time, so a bare
``Config(db_path=...)`` is silently enough to make this file dial production.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database.connection import ConnectionClosedError
from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.observability import SQLiteHealthService


def _sqlite(tmp_path, monkeypatch, name: str = "t.db"):
    """Build a real sqlite connection, immune to DATABASE_URL / .env."""
    monkeypatch.setenv("DATABASE_URL", "")
    conn = get_connection(Config(db_path=str(tmp_path / name), database_url=""))
    assert conn._backend == "sqlite", "refusing to run: factory picked postgres"
    return conn


def _service(tmp_path, monkeypatch):
    """Build a real health service over a real connection from the factory."""
    conn = _sqlite(tmp_path, monkeypatch, name="health.db")
    conn.execute(
        "CREATE TABLE health_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " service TEXT, healthy INTEGER, message TEXT,"
        " latency_ms REAL, timestamp TEXT)"
    )
    conn.commit()
    return conn, SQLiteHealthService(conn)


def test_factory_can_be_forced_onto_sqlite_despite_postgres_env(tmp_path, monkeypatch):
    """The safety property these tests depend on, asserted directly."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@prod.invalid:5432/prod")
    conn = _sqlite(tmp_path, monkeypatch)
    assert conn._backend == "sqlite"
    conn.close()


def test_statement_after_close_raises_typed_error(tmp_path, monkeypatch):
    """The whole point: a distinct, catchable type instead of sqlite's error."""
    conn = _sqlite(tmp_path, monkeypatch)
    conn.close()
    with pytest.raises(ConnectionClosedError):
        conn.execute("SELECT 1")


def test_typed_error_is_not_a_bare_sqlite_programming_error(tmp_path, monkeypatch):
    """Guards against 'fixing' this by re-raising sqlite3.ProgrammingError."""
    conn = _sqlite(tmp_path, monkeypatch)
    conn.close()
    with pytest.raises(ConnectionClosedError) as exc:
        conn.execute("SELECT 1")
    assert not isinstance(exc.value, sqlite3.ProgrammingError)
    assert "closed" in str(exc.value).lower()


@pytest.mark.parametrize("method", ["execute", "executescript", "commit", "rollback", "cursor"])
def test_every_statement_entrypoint_is_guarded(tmp_path, monkeypatch, method):
    """A guard on execute() alone would still crash via the other entrypoints."""
    conn = _sqlite(tmp_path, monkeypatch)
    conn.close()
    with pytest.raises(ConnectionClosedError):
        if method == "execute":
            conn.execute("SELECT 1")
        elif method == "executescript":
            conn.executescript("SELECT 1")
        elif method == "commit":
            conn.commit()
        elif method == "rollback":
            conn.rollback()
        else:
            conn.cursor()


def test_close_is_idempotent(tmp_path, monkeypatch):
    """Teardown paths close more than once; the second close must not raise."""
    conn = _sqlite(tmp_path, monkeypatch)
    conn.close()
    conn.close()
    assert conn.closed is True


def test_background_health_write_after_close_does_not_raise(tmp_path, monkeypatch):
    """The exact D-2 scenario: a worker outlives the connection it writes to."""
    conn, health = _service(tmp_path, monkeypatch)
    conn.close()

    captured: list[BaseException] = []

    def _late_report() -> None:
        try:
            health.report_healthy("late-worker", "ok")
        except BaseException as exc:  # noqa: BLE001
            captured.append(exc)

    worker = threading.Thread(target=_late_report, name="late-health-writer", daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert captured == [], f"background writer raised: {captured!r}"
    # The skip is observable, not silent.
    assert health.history_write_failures == 1
    assert health.services["late-worker"] is True


def test_skipped_write_keeps_returning_status_and_counting(tmp_path, monkeypatch):
    """Repeated late writes stay harmless and keep an auditable tally."""
    conn, health = _service(tmp_path, monkeypatch)
    conn.close()
    for _ in range(3):
        status = health.report_unhealthy("svc", "down")
        assert status.healthy is False
    assert health.history_write_failures == 3


def test_genuine_sql_error_still_propagates(tmp_path, monkeypatch):
    """Fail loudly on real faults -- the closed-DB catch must not mask bugs."""
    conn, health = _service(tmp_path, monkeypatch)
    health.conn.execute("DROP TABLE health_history")
    health.conn.commit()
    # sqlite reports a missing table as OperationalError, which is NOT a
    # ProgrammingError, so the closed-connection guard cannot swallow it.
    with pytest.raises(sqlite3.OperationalError) as exc:
        health.report_healthy("svc", "ok")
    assert not isinstance(exc.value, ConnectionClosedError)
    assert "closed" not in str(exc.value).lower()
    assert health.history_write_failures == 0


def test_healthy_write_before_close_is_recorded(tmp_path, monkeypatch):
    """Sanity: the guard did not disable normal recording."""
    conn, health = _service(tmp_path, monkeypatch)
    health.report_healthy("svc", "ok")
    rows = conn.execute("SELECT service, healthy FROM health_history").fetchall()
    assert [tuple(r) for r in rows] == [("svc", 1)]
    assert health.history_write_failures == 0
