"""Proof for the remote-destructive interlock.

A test run executes ``DROP SCHEMA``, ``DROP TABLE`` and ``TRUNCATE``. The
production database URL sits in a gitignored ``.env`` and ``load_dotenv()``
pulls it into every test process at import time, so the live host is one
mis-aimed ``pytest`` away from being dropped.

These tests pin the interlock. They assert refusals, not just behaviour, and
they include the observation test that the guard was built from: the guard
sees the production-shaped host the .env actually supplies.
"""

from __future__ import annotations

import pytest

from traderos.infrastructure.database.migration_utils import execute
from traderos.infrastructure.database.safety_guard import OPT_IN_ENV
from traderos.infrastructure.database.safety_guard import RemoteDestructiveRefusedError
from traderos.infrastructure.database.safety_guard import guard_connection
from traderos.infrastructure.database.safety_guard import guard_destructive_sql
from traderos.infrastructure.database.safety_guard import is_destructive
from traderos.infrastructure.database.safety_guard import is_loopback
from traderos.infrastructure.database.safety_guard import under_pytest


class FakeRemoteConn:
    """Mimics a psycopg2 connection's host introspection."""

    def __init__(self, host: str) -> None:
        self._host = host

    def get_dsn_parameters(self):
        return {"host": self._host}

    def cursor(self):  # pragma: no cover - never reached; guard raises first
        raise AssertionError("guard must refuse before touching the cursor")


class FakeLocalConn(FakeRemoteConn):
    pass


@pytest.fixture(autouse=True)
def _no_opt_in(monkeypatch):
    """Guarantee the opt-in is off so a developer's shell cannot mask a failure."""
    monkeypatch.delenv(OPT_IN_ENV, raising=False)


# --- classification -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP SCHEMA public CASCADE",
        "DROP TABLE users",
        "drop table users",
        "TRUNCATE trades",
        "DELETE FROM audit_log",
        "  -- leading comment\n  DROP TABLE users",
        "/* block */ DROP TABLE users",
        "\n\n  DROP SCHEMA public CASCADE",
    ],
)
def test_destructive_statements_are_recognised(sql):
    assert is_destructive(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE IF NOT EXISTS t (id INT)",
        "ALTER TABLE t ALTER COLUMN c TYPE NUMERIC(20,8)",
        "SELECT 1",
        "INSERT INTO t VALUES (1)",
        "CREATE INDEX ix ON t(a)",
        "DELETE FROM audit_log WHERE id = 1",
        "SELECT * FROM dropped_table",
    ],
)
def test_harmless_statements_are_not_flagged(sql):
    assert not is_destructive(sql)


def test_delete_with_where_is_not_treated_as_destructive():
    """Row-scoped deletes are ordinary repository work, not schema destruction."""
    assert not is_destructive("DELETE FROM audit_log WHERE id = %s")


# --- host classification --------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5", ""])
def test_loopback_hosts(host):
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host", ["db.weoymywxwmiudephpxet.supabase.co", "10.0.0.4", "prod.example.com"]
)
def test_remote_hosts_are_not_loopback(host):
    assert not is_loopback(host)


# --- the refusals that matter --------------------------------------------


def test_destructive_sql_against_remote_is_refused():
    conn = FakeRemoteConn("db.weoymywxwmiudephpxet.supabase.co")
    with pytest.raises(RemoteDestructiveRefusedError) as exc:
        guard_destructive_sql(conn, "DROP SCHEMA public CASCADE")
    assert "DROP" in str(exc.value)


def test_destructive_sql_against_local_is_allowed():
    """The guard must not impede ordinary local test teardown."""
    guard_destructive_sql(FakeLocalConn("127.0.0.1"), "DROP SCHEMA public CASCADE")
    guard_destructive_sql(FakeLocalConn("localhost"), "DROP TABLE users")


def test_non_destructive_sql_against_remote_is_allowed():
    """Production reads and writes are untouched by this interlock."""
    conn = FakeRemoteConn("db.weoymywxwmiudephpxet.supabase.co")
    guard_destructive_sql(conn, "SELECT 1")
    guard_destructive_sql(conn, "INSERT INTO trades VALUES (1)")
    guard_destructive_sql(conn, "CREATE TABLE IF NOT EXISTS t (id INT)")


def test_opt_in_allows_remote_destructive(monkeypatch):
    monkeypatch.setenv(OPT_IN_ENV, "1")
    guard_destructive_sql(FakeRemoteConn("db.example.com"), "DROP TABLE users")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_opt_in_spellings(monkeypatch, value):
    monkeypatch.setenv(OPT_IN_ENV, value)
    guard_destructive_sql(FakeRemoteConn("db.example.com"), "DROP TABLE users")


def test_refusal_never_reaches_the_database():
    """Assert the refusal happens before any SQL is issued."""
    conn = FakeRemoteConn("db.example.com")
    with pytest.raises(RemoteDestructiveRefusedError):
        execute(conn, "DROP SCHEMA public CASCADE")
    # FakeRemoteConn.cursor() raises AssertionError if reached; no error of
    # that kind appeared, so the guard fired first.


# --- connection guard -----------------------------------------------------


def test_pytest_will_not_dial_a_remote_database(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic::test (call)")
    with pytest.raises(RemoteDestructiveRefusedError) as exc:
        guard_connection("postgresql://user:pw@db.weoymywxwmiudephpxet.supabase.co:5432/postgres")
    assert "supabase.co" in str(exc.value)


def test_pytest_may_dial_a_local_database(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic::test (call)")
    guard_connection("postgresql://traderos:pw@127.0.0.1:5432/traders_test")
    guard_connection("postgresql://traderos:pw@localhost:5432/traders_test")


def test_outside_pytest_remote_is_allowed(monkeypatch):
    """Production and CLI runs must not be blocked by a test-safety interlock.

    With PYTEST_CURRENT_TEST absent the guard stands down entirely, so a live
    migration or CLI boot against the real database still works.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert not under_pytest()
    guard_connection("postgresql://user:pw@db.weoymywxwmiudephpxet.supabase.co:5432/postgres")


def test_connection_opt_in(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic::test (call)")
    monkeypatch.setenv(OPT_IN_ENV, "1")
    guard_connection("postgresql://user:pw@db.example.com:5432/prod")


def test_sqlite_is_never_refused(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic::test (call)")
    guard_connection("sqlite:///data/trader.db")
    guard_connection("")


# --- the observation that motivated this ----------------------------------


def test_production_shaped_url_is_recognised_as_remote(monkeypatch):
    """Pins the exact host shape .env supplies, so the guard cannot rot."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic::test (call)")
    url = "postgresql://postgres.xxxx:PASSWORD@db.weoymywxwmiudephpxet.supabase.co:5432/postgres"
    assert not is_loopback("db.weoymywxwmiudephpxet.supabase.co")
    with pytest.raises(RemoteDestructiveRefusedError) as exc:
        guard_connection(url)
    assert "supabase.co" in str(exc.value)


def test_error_message_does_not_echo_the_password():
    """A refusal is logged; it must not become a credential leak."""
    with pytest.raises(RemoteDestructiveRefusedError) as exc:
        guard_destructive_sql(FakeRemoteConn("db.example.com"), "DROP TABLE users")
    assert "SUPERSECRETPW" not in str(exc.value)
    assert "postgres://" not in str(exc.value)


# --- the real path, end to end -------------------------------------------
#
# Everything above tests the guard in isolation. This one drives the actual
# production entry point -- Config.load() picking the URL up from .env via
# load_dotenv(), then get_connection() -- because that is the path an
# accidental run takes. A guard that is not wired into this path protects
# nothing; that is the failure mode this assertion exists to prevent.


def test_real_get_connection_refuses_the_url_dotenv_supplies(monkeypatch):
    from traderos.infrastructure.config.config_loader import Config
    from traderos.infrastructure.database.connection import get_connection

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.abc:SUPERSECRET@db.weoymywxwmiudephpxet.supabase.co:5432/postgres",
    )
    with pytest.raises(RemoteDestructiveRefusedError) as exc:
        get_connection(Config.load())
    message = str(exc.value)
    assert "refusing" in message.lower()
    assert "SUPERSECRET" not in message, "refusal leaked the password"


def test_real_get_connection_still_reaches_a_local_database(monkeypatch, tmp_path):
    """The interlock must not make the local database unreachable."""
    from traderos.infrastructure.config.config_loader import Config
    from traderos.infrastructure.database.connection import get_connection

    monkeypatch.setenv("DATABASE_URL", "")
    conn = get_connection(Config(db_path=str(tmp_path / "local.db"), database_url=""))
    try:
        assert conn._backend == "sqlite"
    finally:
        conn.close()
