"""Proof for the decoupled PG schema version marker (D-3).

Observed: ``_schema_version`` sat at the head version while ``audit_log`` did
not exist. ``migrate()`` compares the marker against the target, so it no-opped
and the application started against a schema that was missing a table it
believed it had. The marker was treated as proof of the schema; it is only a
claim.

These tests pin the corrected contract on a real PostgreSQL database:

1. ``schema_drift()`` reports declared-but-missing tables per applied version.
2. ``migrate(repair=False)`` fails closed with ``SchemaDriftError``.
3. ``migrate()`` repairs the missing tables without destroying existing rows.
4. A healthy schema reports no drift.
"""

from __future__ import annotations

import pytest

from traderos.infrastructure.database.migration_manager import SchemaDriftError
from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import latest_version
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.database.migration_manager import schema_drift

psycopg2 = pytest.importorskip("psycopg2")


@pytest.fixture
def conn():
    """A real, empty PostgreSQL schema. No mocks: drift is a database fact."""
    dsn = __import__("os").environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN not set")
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    with connection.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    yield connection
    with connection.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    connection.close()


def _drop(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def _exists(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (table,))
        return cur.fetchone()[0] is not None


def test_healthy_schema_reports_no_drift(conn):
    migrate(conn)
    assert schema_drift(conn) == {}


def test_marker_at_head_with_missing_table_is_reported_as_drift(conn):
    """The exact D-3 symptom: marker says head, table is gone."""
    migrate(conn)
    assert get_current_version(conn) == latest_version()

    _drop(conn, "audit_log")
    assert not _exists(conn, "audit_log")

    drift = schema_drift(conn)
    assert 2 in drift
    assert "audit_log" in drift[2]
    assert "audit_log" not in drift[2][1:]  # no false positives


def test_reports_every_missing_table_of_that_migration(conn):
    migrate(conn)
    _drop(conn, "audit_log")
    _drop(conn, "health_history")
    assert set(schema_drift(conn)[2]) == {"audit_log", "health_history"}


def test_drift_spanning_several_migrations(conn):
    migrate(conn)
    _drop(conn, "audit_log")  # v002
    _drop(conn, "users")  # v008
    drift = schema_drift(conn)
    assert set(drift) == {2, 8}


def test_migrate_without_repair_fails_closed(conn):
    """No silent no-op: the caller must be told, not quietly assumed fine."""
    migrate(conn)
    _drop(conn, "audit_log")
    with pytest.raises(SchemaDriftError) as exc:
        migrate(conn, repair=False)
    assert "audit_log" in str(exc.value)
    assert exc.value.drift[2] == ("audit_log",)
    assert not _exists(conn, "audit_log")


def test_migrate_repairs_missing_table(conn):
    migrate(conn)
    _drop(conn, "audit_log")
    migrate(conn)
    assert _exists(conn, "audit_log")
    assert schema_drift(conn) == {}
    assert get_current_version(conn) == latest_version()


def test_repair_preserves_existing_rows(conn):
    """Repair must recreate the table without wiping data in surviving ones."""
    migrate(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO health_history (service, healthy, message, timestamp)"
            " VALUES ('svc', 1, 'ok', CURRENT_TIMESTAMP)"
        )
    _drop(conn, "audit_log")

    migrate(conn)

    assert _exists(conn, "audit_log")
    with conn.cursor() as cur:
        cur.execute("SELECT service FROM health_history")
        assert cur.fetchall() == [("svc",)]


def test_repeated_migrate_is_idempotent(conn):
    migrate(conn)
    first = get_current_version(conn)
    migrate(conn)
    migrate(conn)
    assert get_current_version(conn) == first
    assert schema_drift(conn) == {}


def test_repair_of_multiple_migrations(conn):
    migrate(conn)
    _drop(conn, "audit_log")
    _drop(conn, "users")
    migrate(conn)
    assert _exists(conn, "audit_log")
    assert _exists(conn, "users")
    assert schema_drift(conn) == {}
