from __future__ import annotations

import os

import psycopg2
import pytest

from traderos.domain.ports import HealthPort
from traderos.infrastructure.observability_postgres import PostgresAuditService
from traderos.infrastructure.observability_postgres import PostgresHealthService
from traderos.infrastructure.observability_postgres import PostgresManifestService
from traderos.infrastructure.observability_postgres import PostgresMetricsService

DSN = os.environ.get(
    "POSTGRES_TEST_DSN",
    "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
)

_OBSERVABILITY_TABLES = (
    "audit_log",
    "metrics_history",
    "health_history",
    "run_manifest",
)


def _create_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                resource TEXT NOT NULL,
                detail TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                id_seq SERIAL NOT NULL
            )
            """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS metrics_history (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp TEXT NOT NULL,
                tags TEXT DEFAULT '{}'
            )
            """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS health_history (
                id SERIAL PRIMARY KEY,
                service TEXT NOT NULL,
                healthy INTEGER NOT NULL,
                message TEXT DEFAULT '',
                latency_ms REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL
            )
            """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_manifest (
                id SERIAL PRIMARY KEY,
                run_id TEXT NOT NULL,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """)


@pytest.fixture
def pg_conn():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    _create_schema(conn)
    yield conn
    with conn.cursor() as cur:
        for table in _OBSERVABILITY_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.close()


def _fresh(conn):
    return psycopg2.connect(DSN)


class TestPostgresAuditService:
    def test_record_creates_linked_hashed_entries(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresAuditService(fresh)
        first = svc.record("login", "alice", "system", "session start")
        second = svc.record("trade", "alice", "BTC/USD", "buy 0.1")
        assert first.hash != ""
        assert second.previous_hash == first.hash
        assert svc.verify_chain() is True

    def test_get_entries_orders_newest_first_with_limit_offset(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresAuditService(fresh)
        for i in range(5):
            svc.record("login", "alice", f"resource-{i}")
        entries = svc.get_entries(limit=2, offset=0)
        assert len(entries) == 2
        assert entries[0].resource == "resource-4"
        assert entries[1].resource == "resource-3"

    def test_find_filters_by_action_and_actor(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresAuditService(fresh)
        svc.record("login", "alice", "system")
        svc.record("trade", "alice", "BTC/USD")
        svc.record("trade", "bob", "ETH/USD")
        assert [e.actor for e in svc.find(action="trade")] == ["alice", "bob"]
        assert [e.resource for e in svc.find(actor="bob")] == ["ETH/USD"]
        assert len(svc.find()) == 3

    def test_find_filters_by_both(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresAuditService(fresh)
        svc.record("trade", "alice", "BTC/USD")
        svc.record("trade", "bob", "ETH/USD")
        assert [e.resource for e in svc.find(action="trade", actor="bob")] == ["ETH/USD"]


class TestPostgresMetricsService:
    def test_counter_increments_and_persists(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresMetricsService(fresh)
        assert svc.counter("orders") == 1.0
        assert svc.counter("orders") == 2.0
        assert svc.get_counter("orders") == 2.0
        assert svc.query("orders", limit=10)[0].value == 2.0

    def test_gauge_and_snapshot(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresMetricsService(fresh)
        svc.gauge("portfolio", 1234.5)
        assert svc.get_gauge("portfolio") == 1234.5
        assert svc.snapshot() == {"portfolio": 1234.5}

    def test_timing_records_elapsed_ms(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresMetricsService(fresh)
        with svc.timing("cycle.duration_ms") as timer:
            elapsed = timer.stop()
        assert elapsed >= 0.0
        assert svc.get_gauge("cycle.duration_ms") is not None

    def test_query_returns_metric_samples(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresMetricsService(fresh)
        svc.counter("orders")
        samples = svc.query("orders")
        assert samples[0].name == "orders"
        assert samples[0].value == 1.0
        assert samples[0].tags == {}

    def test_clear_empties_memory_and_history(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresMetricsService(fresh)
        svc.counter("orders")
        svc.clear()
        assert svc.get_counter("orders") == 0.0
        assert svc.query("orders") == []


class TestPostgresHealthService:
    def test_register_and_status(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)
        svc.register("broker", initial=True)
        assert svc.get_status("broker") is True

    def test_report_healthy_and_unhealthy(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)
        svc.report_healthy("broker")
        assert svc.all_healthy() is True
        svc.report_unhealthy("broker", "api down")
        assert svc.all_healthy() is False
        assert svc.summary() == {"broker": False}

    def test_check_returns_status_and_saves_history(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)
        passed = svc.check("broker", lambda: True)
        assert passed.healthy is True
        failed = svc.check("data_feed", lambda: False)
        assert failed.healthy is False
        assert len(svc.history(limit=10)) >= 2

    def test_check_handles_exception_as_unhealthy(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)

        def boom():
            raise RuntimeError("timeout")

        status = svc.check("data_feed", boom)
        assert status.healthy is False
        assert status.message == "timeout"

    def test_all_healthy_empty(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)
        assert svc.all_healthy() is True

    def test_health_port_conformance(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresHealthService(fresh)
        assert isinstance(svc, HealthPort)


class TestPostgresManifestService:
    def test_record_and_get_runs(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresManifestService(fresh)
        entry = svc.record(
            "backtest", "run", status="completed", duration_ms=12.5, metadata={"n": 1}
        )
        assert entry.run_id == entry.run_id
        runs = svc.get_runs(limit=10)
        assert len(runs) == 1
        assert runs[0].service == "backtest"
        assert runs[0].metadata == {"n": 1}

    def test_get_runs_filters_by_service(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresManifestService(fresh)
        svc.record("backtest", "run")
        svc.record("research", "observe")
        assert len(svc.get_runs(service="backtest")) == 1
        assert len(svc.get_runs()) == 2

    def test_summary_and_clear(self, pg_conn):
        fresh = _fresh(pg_conn)
        fresh.autocommit = True
        svc = PostgresManifestService(fresh)
        svc.record("backtest", "run")
        svc.record("backtest", "run")
        assert svc.summary() == {"backtest": 2}
        svc.clear()
        assert svc.summary() == {}
