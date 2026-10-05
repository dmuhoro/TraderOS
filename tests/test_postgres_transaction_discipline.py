"""Proof that the PostgreSQL layer does not leak connections or transactions.

DEFECT (found by running the FULL suite, not a subset): with a real
``DATABASE_URL`` the suite wedges permanently in
``tests/test_postgres_repositories.py`` teardown. ``pg_stat_activity`` showed
15-17 backends parked in ``idle in transaction``, 15 of them *blocking* the
fixture's ``DROP TABLE ... CASCADE``:

    132426|active|Lock|relation|DROP TABLE IF EXISTS strategies CASCADE

Two compounding causes, both on the real production path:

1. ``_connect_postgres`` sets ``autocommit = False``, but repository READ
   methods never commit or roll back. A read therefore leaves an open
   transaction that pins row locks for the life of the process.
2. ``get_connection`` calls ``psycopg2.connect`` with no pool and no close
   path, so each caller leaks a whole backend.

Together these exhaust ``max_connections`` (100 on the test container) and
block every subsequent DDL -- i.e. they block migrations in production, not
just the test suite.

These tests assert the CORRECT behaviour, so they fail on the pre-fix code.
They use a real PostgreSQL connection and a real repository -- no mocks --
because the defect is entirely about server-side transaction state, which a
mock cannot exhibit.
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.repositories.postgres.users import PostgresUserRepository

DSN = os.environ.get(
    "POSTGRES_TEST_DSN",
    "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
)


def _pg_reachable(dsn: str, timeout: int = 3) -> bool:
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        conn.close()
        return True
    except psycopg2.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(DSN),
    reason=f"Postgres not reachable at {DSN} — skipped, not passed",
)


def _as_url(dsn: str) -> str:
    """Convert a libpq keyword DSN into the URL form get_connection() reads."""
    parts: dict[str, str] = {}
    for token in dsn.split():
        key, _, value = token.partition("=")
        parts[key] = value
    user = parts.get("user", "postgres")
    password = parts.get("password", "")
    host = parts.get("host", "localhost")
    port = parts.get("port", "5432")
    dbname = parts.get("dbname", "postgres")
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{dbname}"


@pytest.fixture
def observer():
    """Separate autocommit connection used to inspect backend state.

    The state of a backend is only observable as ``idle in transaction`` from
    a *different* session — asking a backend about itself always reports
    ``active`` while its own query is running.
    """
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def repo(monkeypatch):
    """Real repository on the real production connect path.

    ``get_connection`` is the function the factory and boot sequence call; it
    is what sets ``autocommit = False``. Patching DATABASE_URL here makes the
    test independent of whatever the ambient environment points at.
    """
    monkeypatch.setenv("DATABASE_URL", _as_url(DSN))
    conn = get_connection()
    try:
        yield PostgresUserRepository(conn)
    finally:
        conn.rollback()
        conn.close()


def _backend_state(observer, pid: int) -> str:
    with observer.cursor() as cur:
        cur.execute("SELECT state FROM pg_stat_activity WHERE pid = %s", (pid,))
        row = cur.fetchone()
    return row[0] if row else "gone"


def _repo_pid(repo) -> int:
    return repo.conn.get_backend_pid()


def test_single_read_does_not_leave_backend_idle_in_transaction(repo, observer) -> None:
    """A read must not leave an open transaction pinning locks.

    This is the minimal reproduction: one SELECT through the real repository.
    Pre-fix the backend lands in ``idle in transaction``.
    """
    repo.conn.rollback()
    pid = _repo_pid(repo)

    repo.get_user_by_username("definitely-not-a-real-user")

    state = _backend_state(observer, pid)
    assert state != "idle in transaction", (
        "read left the PostgreSQL backend in 'idle in transaction'; it is "
        "holding row locks and will block DDL/migrations. autocommit=False "
        "with no commit/rollback on the read path."
    )


def test_repeated_reads_do_not_accumulate_open_transactions(repo, observer) -> None:
    """Many reads must not leave many open transactions behind.

    Pre-fix, each read on a non-autocommit connection opens a transaction
    that is never closed, so the server accumulates them until it blocks DDL.
    """
    repo.conn.rollback()
    pid = _repo_pid(repo)

    for _ in range(25):
        repo.get_user_by_username("definitely-not-a-real-user")

    state = _backend_state(observer, pid)
    assert state != "idle in transaction", (
        "after 25 reads the backend is still 'idle in transaction' — reads "
        "are leaking open transactions"
    )


def test_read_path_leaves_no_open_transaction_per_driver(repo) -> None:
    """Driver-level proof, independent of pg_stat_activity sampling.

    ``psycopg2`` reports IDLE only when no transaction is open. After a read
    the connection must be IDLE; pre-fix it is INTRANS, which is precisely
    the state that pins locks and blocks DDL.
    """
    repo.conn.rollback()
    assert repo.conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE

    repo.get_user_by_username("definitely-not-a-real-user")

    status = repo.conn.get_transaction_status()
    assert status == psycopg2.extensions.TRANSACTION_STATUS_IDLE, (
        "after a read the connection is not IDLE — a transaction is still "
        "open (INTRANS), holding row locks and able to block migrations"
    )


_IDLE = psycopg2.extensions.TRANSACTION_STATUS_IDLE


def _repositories():
    """Every postgres repository whose read path must end its transaction.

    Listed explicitly rather than discovered so that a NEW repository cannot
    silently join the leaking set: adding a repo here is a deliberate act.
    """
    import uuid

    from traderos.infrastructure.repositories.postgres.knowledge import (
        PostgresKnowledgeEdgeRepository,
    )
    from traderos.infrastructure.repositories.postgres.knowledge import (
        PostgresKnowledgeNodeRepository,
    )
    from traderos.infrastructure.repositories.postgres.research import PostgresExperimentRepository
    from traderos.infrastructure.repositories.postgres.research import PostgresObservationRepository
    from traderos.infrastructure.repositories.postgres.signals import PostgresSignalRepository
    from traderos.infrastructure.repositories.postgres.strategies import PostgresStrategyRepository
    from traderos.infrastructure.repositories.postgres.trades import PostgresPositionRepository
    from traderos.infrastructure.repositories.postgres.trades import PostgresTradeRepository

    probe = uuid.uuid4()
    return [
        (PostgresKnowledgeNodeRepository, lambda r: r.get_by_label("absent")),
        (PostgresKnowledgeEdgeRepository, lambda r: r.get_by_source(probe)),
        (PostgresObservationRepository, lambda r: r.get_by_symbol("ABSENT")),
        (PostgresExperimentRepository, lambda r: r.get_by_hypothesis(probe)),
        (PostgresSignalRepository, lambda r: r.get_active(probe)),
        (PostgresStrategyRepository, lambda r: r.get_by_name("absent")),
        (PostgresStrategyRepository, lambda r: r.list_active()),
        (PostgresTradeRepository, lambda r: r.get_by_market(probe)),
        (PostgresTradeRepository, lambda r: r.get_open()),
        (PostgresPositionRepository, lambda r: r.get_by_market(probe)),
        (PostgresPositionRepository, lambda r: r.list_open()),
    ]


@pytest.mark.parametrize("cls,invoke", _repositories(), ids=lambda v: getattr(v, "__name__", ""))
def test_every_repository_read_ends_its_transaction(monkeypatch, cls, invoke) -> None:
    """No postgres repository read may leave a transaction open.

    This is the regression guard for the whole defect class, not just the
    users repository. Pre-fix, each of these reads left the backend in
    ``idle in transaction``.
    """
    monkeypatch.setenv("DATABASE_URL", _as_url(DSN))
    conn = get_connection()
    try:
        repo = cls(conn)
        conn.rollback()  # discard the CREATE TABLE IF NOT EXISTS transaction
        assert conn.get_transaction_status() == _IDLE

        invoke(repo)

        assert conn.get_transaction_status() == _IDLE, (
            f"{cls.__name__} read left a transaction open (INTRANS); it holds "
            f"row locks and can block DDL/migrations"
        )
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
