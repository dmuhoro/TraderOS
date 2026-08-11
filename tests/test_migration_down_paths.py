"""Migration down() paths + migration_manager downgrade guard (OT-005).

A real downgrade all the way to version 0 runs v008..v001 ``down()``, which is
the only way the oldest migrations' rollbacks (v001/v002/v003) execute in a
test. Also proves the non-int ``target_version`` guard and the two v006
defensive branches (empty sqlite PRAGMA, Postgres legacy ``strategies`` rebuild).
"""

from __future__ import annotations

import os
import sqlite3

import psycopg2
import pytest

from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.database.migrations.v006_operator_surface import _is_legacy_strategies
from traderos.infrastructure.database.migrations.v006_operator_surface import up as v006_up

PG_DSN = os.environ.get(
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


class TestFullDowngradeChain:
    def test_migrate_down_to_zero_drops_every_v001_v002_v003_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        migrate(conn)
        assert get_current_version(conn) == 8
        for table in ("market_data", "observations", "risk_limits"):
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            assert row is not None, f"table {table} should exist after up"

        migrate(conn, target_version=0)

        assert get_current_version(conn) == 0
        dropped = [
            "market_data",
            "features",
            "correlations",
            "journal_entries",
            "liquidity_zones",
            "market_structure_events",
            "session_statistics",
            "observations",
            "hypotheses",
            "research_tests",
            "research_results",
            "lessons",
            "risk_limits",
            "audit_log",
            "metrics_history",
            "health_history",
            "run_manifest",
            "strategy_registry",
        ]
        for table in dropped:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            assert row is None, f"table {table} should be dropped after full downgrade"
        count = conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0]
        assert count == 0
        conn.close()

    def test_migrate_down_skips_already_dropped_tables(self) -> None:
        conn = sqlite3.connect(":memory:")
        migrate(conn)
        migrate(conn, target_version=0)
        migrate(conn, target_version=0)
        assert get_current_version(conn) == 0
        conn.close()


class TestMigrationManagerGuard:
    def test_non_int_target_version_raises_type_error(self) -> None:
        conn = sqlite3.connect(":memory:")
        with pytest.raises(TypeError, match="target_version must be int"):
            migrate(conn, target_version="5")
        conn.close()


class _EmptyRowsCursor:
    def fetchall(self) -> list[object]:
        return []

    def fetchone(self) -> None:
        return None


class _FakeSqliteConn:
    _backend = "sqlite"

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _EmptyRowsCursor:
        self.executed.append(sql)
        return _EmptyRowsCursor()

    def commit(self) -> None:
        pass


class TestV006DefensiveBranches:
    def test_sqlite_legacy_detection_empty_pragma_is_not_legacy(self) -> None:
        conn = _FakeSqliteConn()
        assert _is_legacy_strategies(conn, "sqlite") is False


@pytest.fixture
def pg_conn():
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS backtest_results")
        cur.execute("DROP TABLE IF EXISTS strategies_legacy")
        cur.execute("DROP TABLE IF EXISTS strategies")
    yield conn
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS backtest_results")
        cur.execute("DROP TABLE IF EXISTS strategies_legacy")
        cur.execute("DROP TABLE IF EXISTS strategies")
    conn.close()


@pytest.mark.skipif(
    not _pg_reachable(PG_DSN),
    reason=f"Postgres not reachable at {PG_DSN} — skipped, not passed",
)
class TestV006PostgresLegacyRebuild:
    def test_up_rebuilds_legacy_strategies_and_drops_backtest_results(self, pg_conn) -> None:
        with pg_conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE strategies ("
                " id INTEGER PRIMARY KEY,"
                " name TEXT,"
                " params_json TEXT,"
                " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            cur.execute(
                "CREATE TABLE backtest_results ("
                " id TEXT PRIMARY KEY,"
                " strategy_id INTEGER REFERENCES strategies(id))"
            )
        v006_up(pg_conn, backend="postgres")
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name = 'strategies' AND column_name = 'id'"
            )
            assert cur.fetchone()[0].lower() == "text"
            cur.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'strategies' AND column_name = 'params'"
            )
            assert cur.fetchone() is not None
            cur.execute("SELECT to_regclass('backtest_results')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT to_regclass('strategies_legacy')")
            assert cur.fetchone()[0] is None
