from __future__ import annotations

import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from traderos.infrastructure.archiver import purge_old_entries
from traderos.infrastructure.database.migration_manager import migrate


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test.db")
    migrate(conn)
    now = datetime.now(UTC)
    old = (now - timedelta(days=120)).isoformat()
    recent = now.isoformat()
    conn.execute(
        "INSERT INTO audit_log (id, action, actor, resource, timestamp, previous_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("audit-old", "act", "system", "res", old, "0" * 64, "a" * 64),
    )
    conn.execute(
        "INSERT INTO audit_log (id, action, actor, resource, timestamp, previous_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("audit-new", "act", "system", "res", recent, "a" * 64, "b" * 64),
    )
    conn.execute(
        "INSERT INTO order_events (id, trade_id, status, payload, published, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("oe-old", "t1", "applied", "{}", 1, old),
    )
    conn.execute(
        "INSERT INTO order_events (id, trade_id, status, payload, published, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("oe-new", "t2", "applied", "{}", 1, recent),
    )
    conn.commit()
    return conn


class TestPurgeOldEntries:
    def test_purges_expired_rows(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = purge_old_entries(conn, retention_days=90)
        assert "audit_log" in result
        assert "order_events" in result
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id FROM audit_log").fetchall()
        assert {r["id"] for r in rows} == {"audit-new"}
        rows = conn.execute("SELECT id FROM order_events").fetchall()
        assert {r["id"] for r in rows} == {"oe-new"}
        conn.close()

    def test_respects_recent_rows(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = purge_old_entries(conn, retention_days=365)
        assert result == {}
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT COUNT(*) AS n FROM order_events").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 2
        conn.close()


def _pg_reachable(timeout: int = 3) -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(
            "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
            connect_timeout=timeout,
        )
        conn.close()
        return True
    except Exception:  # noqa: BLE001 — environment probe, never fatal
        return False


import pytest  # noqa: E402


@pytest.mark.skipif(
    not _pg_reachable(),
    reason="Postgres not reachable — archiver PG regression skipped, not passed",
)
class TestPurgeKeepsPgConnectionUsable:
    """A5 regression: the archiver must not poison a PostgreSQL connection.

    When any purge table/column is missing, the DELETE fails and — unless we
    roll back — the transaction stays aborted, making every subsequent repo's
    CREATE TABLE fail with InFailedSqlTransaction. The factory builds the PG
    repos on the same connection used by purge, so this would break ANY
    PG-backed orchestrator boot.
    """

    def test_missing_table_does_not_poison_connection(self) -> None:
        import psycopg2

        dsn = "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos"
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS audit_log")
            conn.commit()
            from traderos.infrastructure.archiver import purge_old_entries

            purge_old_entries(conn, retention_days=90)
            # The connection must now be usable for a real statement.
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
            conn.rollback()
            # Restore the dropped audit_log table so the shared test store is
            # left in a clean, migrated state for other tests.
            from traderos.infrastructure.database.migration_manager import migrate

            migrate(conn)
            conn.commit()
        finally:
            conn.rollback()
            conn.close()
