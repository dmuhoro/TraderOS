"""Proof that v012 realigns a legacy research schema instead of assuming one.

The defect v012 exists for is only visible on a database created before the
repository DDL was adopted, so every test here starts by building that legacy
shape by hand rather than running the current migrations.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from traderos.infrastructure.database.migration_manager import get_current_version
from traderos.infrastructure.database.migration_manager import latest_version
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.database.migration_manager import schema_drift
from traderos.infrastructure.database.migrations import v012_research_schema_alignment as v012

_LEGACY = """
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT,
    content TEXT NOT NULL,
    tags TEXT
);
CREATE TABLE hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE research_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    test_params TEXT,
    results_summary TEXT
);
CREATE TABLE research_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metrics_json TEXT,
    visual_path TEXT
);
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    content TEXT NOT NULL,
    tags TEXT
);
"""


def _legacy_with_data() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY)
    conn.execute(
        "INSERT INTO observations (symbol, content, tags) VALUES ('BTC/USDT', 'sweep', '[]')"
    )
    conn.execute("INSERT INTO hypotheses (observation_id, content) VALUES (1, 'supply zone')")
    conn.execute(
        "INSERT INTO research_tests (hypothesis_id, test_params) VALUES (1, ?)",
        ('{"period": "90d"}',),
    )
    conn.execute(
        "INSERT INTO research_results (test_id, metrics_json, visual_path) "
        "VALUES (1, '{\"win_rate\": 0.65}', '')"
    )
    conn.execute("INSERT INTO lessons (result_id, content, tags) VALUES (1, 'wait', '[]')")
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: str(row[2]).upper() for row in conn.execute(f"PRAGMA table_info({table})")}


class TestV012ResearchSchemaAlignment:
    def test_is_the_migration_head(self) -> None:
        assert v012.VERSION == latest_version()

    def test_legacy_ids_become_text_keys(self) -> None:
        conn = _legacy_with_data()
        v012.up(conn)
        for table, _ddl, _refs in v012._SPEC:
            assert _columns(conn, table)["id"] == "TEXT", table

    def test_legacy_timestamps_become_created_at(self) -> None:
        conn = _legacy_with_data()
        v012.up(conn)
        assert "timestamp" not in _columns(conn, "hypotheses")
        assert _columns(conn, "hypotheses")["created_at"] == "TEXT"
        assert _columns(conn, "lessons")["created_at"] == "TEXT"

    def test_child_references_still_resolve(self) -> None:
        """The join chain must survive the id remapping."""
        conn = _legacy_with_data()
        v012.up(conn)
        row = conn.execute("""
            SELECT o.content, h.content, t.test_params, r.metrics_json, l.content
            FROM lessons l
            JOIN research_results r ON l.result_id = r.id
            JOIN research_tests t ON r.test_id = t.id
            JOIN hypotheses h ON t.hypothesis_id = h.id
            JOIN observations o ON h.observation_id = o.id
            """).fetchone()
        assert row == ("sweep", "supply zone", '{"period": "90d"}', '{"win_rate": 0.65}', "wait")

    def test_row_count_preserved(self) -> None:
        conn = _legacy_with_data()
        v012.up(conn)
        for table, _ddl, _refs in v012._SPEC:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table

    def test_legacy_datetime_rendered_as_iso8601(self) -> None:
        conn = _legacy_with_data()
        v012.up(conn)
        created = conn.execute("SELECT created_at FROM hypotheses").fetchone()[0]
        assert created.endswith("+00:00"), created
        assert "T" in created, created

    def test_is_idempotent(self) -> None:
        conn = _legacy_with_data()
        v012.up(conn)
        v012.up(conn)
        assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1

    def test_noop_on_canonical_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        for _table, ddl, _refs in v012._SPEC:
            conn.execute(ddl.format(t=_table))
        v012.up(conn)
        assert _columns(conn, "observations")["id"] == "TEXT"

    def test_missing_tables_are_skipped(self) -> None:
        conn = sqlite3.connect(":memory:")
        v012.up(conn)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0

    def test_down_is_a_declined_noop(self) -> None:
        conn = _legacy_with_data()
        assert v012.down(conn, "sqlite") is None
        assert _columns(conn, "observations")["id"] == "INTEGER"


class TestRepositoryWriterOnAlignedSchema:
    """The engine's INSERTs must work against the shape v012 produces."""

    def test_research_engine_round_trip(self) -> None:
        from traderos.domain.research.research_engine import ResearchEngine

        class _DB:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self.conn = conn

        conn = _legacy_with_data()
        v012.up(conn)
        engine = ResearchEngine(_DB(conn))
        oid = engine.create_observation("ETH/USDT", "breakout", '["a"]')
        hid = engine.create_hypothesis(oid, "trend continues")
        tid = engine.create_test(hid, {"period": "30d"})
        rid = engine.record_result(tid, {"win_rate": 0.5})
        lid = engine.record_lesson(rid, "let it ride", "[]")
        workflow = engine.get_full_workflow(lid)
        assert workflow["observation"] == "breakout"
        assert workflow["hypothesis"] == "trend continues"


def _pg_connect():
    import psycopg2

    dsn = os.environ.get(
        "POSTGRES_TEST_DSN",
        "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
    )
    try:
        conn = psycopg2.connect(dsn, connect_timeout=3)
    except psycopg2.Error:
        return None
    conn.autocommit = True
    return conn


_LEGACY_PG = """
DROP TABLE IF EXISTS lessons CASCADE; DROP TABLE IF EXISTS research_results CASCADE;
DROP TABLE IF EXISTS research_tests CASCADE; DROP TABLE IF EXISTS hypotheses CASCADE;
DROP TABLE IF EXISTS observations CASCADE;
CREATE TABLE observations (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT, content TEXT NOT NULL, tags TEXT);
CREATE TABLE hypotheses (id SERIAL PRIMARY KEY, observation_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, content TEXT NOT NULL,
    status TEXT DEFAULT 'pending');
CREATE TABLE research_tests (id SERIAL PRIMARY KEY, hypothesis_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, test_params TEXT, results_summary TEXT);
CREATE TABLE research_results (id SERIAL PRIMARY KEY, test_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metrics_json TEXT,
    visual_path TEXT);
CREATE TABLE lessons (id SERIAL PRIMARY KEY, result_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, content TEXT NOT NULL, tags TEXT);
INSERT INTO observations (symbol, content, tags) VALUES ('BTC/USDT', 'sweep', '[]');
INSERT INTO hypotheses (observation_id, content) VALUES (1, 'supply zone');
INSERT INTO research_tests (hypothesis_id, test_params) VALUES (1, '{"period": "90d"}');
INSERT INTO research_results (test_id, metrics_json, visual_path)
    VALUES (1, '{"win_rate": 0.65}', '');
INSERT INTO lessons (result_id, content, tags) VALUES (1, 'wait', '[]');
"""


class TestV012OnPostgres:
    """The PG path is a different implementation (ALTER, not rebuild), so it needs
    its own proof that the join survives and the column names converge."""

    def test_columns_match_repository_ddl(self) -> None:
        conn = _pg_connect()
        if conn is None:
            pytest.skip("PostgreSQL test server not reachable")
        cur = conn.cursor()
        try:
            cur.execute(_LEGACY_PG)
            v012.up(conn, "postgres")
            for table in ("observations", "hypotheses", "lessons"):
                cur.execute(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table,),
                )
                rows = {name: (dtype, nullable) for name, dtype, nullable in cur.fetchall()}
                assert rows["id"] == ("text", "NO"), table
                stamp = "timestamp" if table == "observations" else "created_at"
                assert rows[stamp][0] == "text", (table, stamp, rows[stamp])
                assert rows[stamp][1] == "NO", (table, stamp, rows[stamp])
        finally:
            conn.close()

    def test_legacy_timestamp_default_is_dropped(self) -> None:
        """DEFAULT CURRENT_TIMESTAMP on a now-TEXT column is the original bug: it
        hides a writer that omits the value."""
        conn = _pg_connect()
        if conn is None:
            pytest.skip("PostgreSQL test server not reachable")
        cur = conn.cursor()
        try:
            cur.execute(_LEGACY_PG)
            v012.up(conn, "postgres")
            cur.execute(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name = 'hypotheses'"
            )
            defaults = dict(cur.fetchall())
            assert defaults.get("created_at") is None, defaults
        finally:
            conn.close()

    def test_join_survives_and_rows_are_preserved(self) -> None:
        conn = _pg_connect()
        if conn is None:
            pytest.skip("PostgreSQL test server not reachable")
        cur = conn.cursor()
        try:
            cur.execute(_LEGACY_PG)
            v012.up(conn, "postgres")
            cur.execute("""
                SELECT o.content, h.content, t.test_params, r.metrics_json, l.content
                FROM lessons l
                JOIN research_results r ON l.result_id = r.id
                JOIN research_tests t ON r.test_id = t.id
                JOIN hypotheses h ON t.hypothesis_id = h.id
                JOIN observations o ON h.observation_id = o.id
                """)
            assert cur.fetchone() == (
                "sweep",
                "supply zone",
                '{"period": "90d"}',
                '{"win_rate": 0.65}',
                "wait",
            )
        finally:
            conn.close()

    def test_insert_without_id_is_refused(self) -> None:
        conn = _pg_connect()
        if conn is None:
            pytest.skip("PostgreSQL test server not reachable")
        cur = conn.cursor()
        try:
            cur.execute(_LEGACY_PG)
            v012.up(conn, "postgres")
            import psycopg2.errors

            with pytest.raises(psycopg2.errors.NotNullViolation):
                cur.execute(
                    "INSERT INTO hypotheses (observation_id, content, created_at) "
                    "VALUES ('x', 'y', '2026-01-01T00:00:00+00:00')"
                )
        finally:
            conn.close()

    def test_idempotent(self) -> None:
        conn = _pg_connect()
        if conn is None:
            pytest.skip("PostgreSQL test server not reachable")
        cur = conn.cursor()
        try:
            cur.execute(_LEGACY_PG)
            v012.up(conn, "postgres")
            v012.up(conn, "postgres")
            cur.execute("SELECT COUNT(*) FROM lessons")
            assert cur.fetchone()[0] == 1
        finally:
            conn.close()


class TestV012ThroughTheFullChain:
    """v012 must run as part of migrate() on a database that already recorded
    v001..v011, which is the situation every deployed database is in."""

    def _legacy_at_v11(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(_LEGACY)
        conn.execute(
            "INSERT INTO observations (symbol, content, tags) VALUES ('BTC/USDT', 'sweep', '[]')"
        )
        conn.execute("INSERT INTO hypotheses (observation_id, content) VALUES (1, 'supply')")
        conn.execute(
            "INSERT INTO research_tests (hypothesis_id, test_params) VALUES (1, ?)",
            ('{"p": 1}',),
        )
        conn.execute(
            "INSERT INTO research_results (test_id, metrics_json, visual_path) " "VALUES (1, ?, ?)",
            ('{"wr": 0.65}', ""),
        )
        conn.execute("INSERT INTO lessons (result_id, content, tags) VALUES (1, 'wait', '[]')")
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, "
            "applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.executemany(
            "INSERT INTO schema_version (version) VALUES (?)", [(i,) for i in range(1, 12)]
        )
        conn.commit()
        return conn

    def test_chain_advances_and_preserves_data(self) -> None:
        conn = self._legacy_at_v11()
        migrate(conn, repair=False)
        assert get_current_version(conn) == latest_version() == 12
        assert schema_drift(conn) == {}
        assert conn.execute("""
            SELECT o.content, h.content, t.test_params, r.metrics_json, l.content
            FROM lessons l
            JOIN research_results r ON l.result_id = r.id
            JOIN research_tests t ON r.test_id = t.id
            JOIN hypotheses h ON t.hypothesis_id = h.id
            JOIN observations o ON h.observation_id = o.id
            """).fetchone() == ("sweep", "supply", '{"p": 1}', '{"wr": 0.65}', "wait")


class TestSqliteRejectsNullIdentity:
    """SQLite does not imply NOT NULL for ``TEXT PRIMARY KEY``.

    PostgreSQL refuses a NULL primary key outright; SQLite accepts one unless
    NOT NULL is declared. That asymmetry is what let research tables accumulate
    rows with no identity -- the writer read cursor.lastrowid, received None, and
    the row became unreachable by any join. These tests pin the refusal so the
    repaired schema cannot silently regress on SQLite.
    """

    def test_plain_text_primary_key_accepts_null_in_sqlite(self) -> None:
        """The trap, stated plainly: without NOT NULL, SQLite takes a NULL id."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, content TEXT NOT NULL)")
        conn.execute("INSERT INTO t (id, content) VALUES (NULL, 'x')")
        assert conn.execute("SELECT id FROM t").fetchone()[0] is None

    @pytest.mark.parametrize("table", list(v012._SPEC and [s[0] for s in v012._SPEC]))
    def test_repaired_schema_refuses_null_id(self, table: str) -> None:
        conn = sqlite3.connect(":memory:")
        for name, ddl, _refs in v012._SPEC:
            conn.execute(ddl.format(t=name))
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        placeholders = ", ".join("?" for _ in columns)
        values = [None] + ["x"] * (len(columns) - 1)
        with pytest.raises(sqlite3.IntegrityError):
            column_sql = ", ".join(columns)
            conn.execute(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})", values)

    def test_v001_declares_not_null(self) -> None:
        from traderos.infrastructure.database.migrations import v001_initial

        assert v001_initial._text_pk() == "TEXT PRIMARY KEY NOT NULL"
