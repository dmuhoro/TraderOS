from __future__ import annotations

import os
import sqlite3

import psycopg2
import pytest

from traderos.infrastructure.database.migrations.v004_external_order_id import down
from traderos.infrastructure.database.migrations.v004_external_order_id import up

PG_DSN = os.environ.get(
    "POSTGRES_TEST_DSN",
    "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
)


def _sqlite_trades_without_column() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trades ("
        " id TEXT PRIMARY KEY, signal_id TEXT, market_id TEXT, side TEXT,"
        " quantity REAL, price REAL, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.commit()
    return conn


class TestV004Sqlite:
    def test_up_adds_column_to_existing_table(self) -> None:
        conn = _sqlite_trades_without_column()
        up(conn, backend="sqlite")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "external_order_id" in columns
        conn.close()

    def test_up_skips_when_no_trades_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        up(conn, backend="sqlite")
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trades'"
        ).fetchone()
        assert row is None
        conn.close()

    def test_up_swallows_when_column_already_exists(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, external_order_id TEXT)")
        conn.commit()
        up(conn, backend="sqlite")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "external_order_id" in columns
        conn.close()

    def test_down_drops_column_when_present(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, external_order_id TEXT)")
        conn.commit()
        down(conn, backend="sqlite")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "external_order_id" not in columns
        conn.close()

    def test_down_noop_without_column(self) -> None:
        conn = _sqlite_trades_without_column()
        down(conn, backend="sqlite")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        assert "external_order_id" not in columns
        conn.close()

    def test_down_noop_without_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        down(conn, backend="sqlite")
        conn.close()


@pytest.fixture
def pg_conn():
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    yield conn
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS trades")
    conn.close()


class TestV004Postgres:
    def test_up_adds_column_when_missing(self, pg_conn) -> None:
        with pg_conn.cursor() as cur:
            cur.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, status TEXT)")
        up(pg_conn, backend="postgres")
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'trades' AND column_name = 'external_order_id'"
            )
            assert cur.fetchone() is not None

    def test_up_noop_when_table_missing(self, pg_conn) -> None:
        up(pg_conn, backend="postgres")
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('trades')",
            )
            assert cur.fetchone()[0] is None

    def test_down_drops_column_when_present(self, pg_conn) -> None:
        with pg_conn.cursor() as cur:
            cur.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, external_order_id TEXT)")
        down(pg_conn, backend="postgres")
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'trades' AND column_name = 'external_order_id'"
            )
            assert cur.fetchone() is None

    def test_down_noop_when_table_missing(self, pg_conn) -> None:
        down(pg_conn, backend="postgres")
