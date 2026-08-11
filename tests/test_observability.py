from __future__ import annotations

import sqlite3

import pytest

from traderos.infrastructure.audit import compute_audit_hash
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.observability import TimingContext
from traderos.infrastructure.observability_postgres import TimingContext as PostgresTimingContext


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = str(tmp_path / "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(c)
    return c


class TestSQLiteAuditService:
    def test_record_and_get_entries(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        e = svc.record("test.action", "tester", "resource1", "detail1")
        assert e.action == "test.action"
        assert e.actor == "tester"
        assert e.hash != ""

    def test_get_entries_with_limit(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        for i in range(5):
            svc.record(f"action{i}", "tester", "r")
        entries = svc.get_entries(limit=3)
        assert len(entries) == 3

    def test_get_entries_with_offset(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        for i in range(5):
            svc.record(f"action{i}", "tester", "r")
        entries = svc.get_entries(limit=10, offset=2)
        assert len(entries) == 3

    def test_verify_chain_valid(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        assert svc.verify_chain() is True

    def test_find_by_action(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("action1", "tester", "r")
        svc.record("action2", "tester", "r")
        results = svc.find(action="action1")
        assert len(results) == 1
        assert results[0].action == "action1"

    def test_find_by_actor(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "bob", "r")
        svc.record("a2", "alice", "r")
        results = svc.find(actor="bob")
        assert len(results) == 1
        assert results[0].actor == "bob"

    def test_find_by_action_and_actor(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("login", "bob", "r")
        svc.record("logout", "bob", "r")
        svc.record("login", "alice", "r")
        results = svc.find(action="login", actor="bob")
        assert len(results) == 1

    def test_find_all_when_no_filters(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        for i in range(3):
            svc.record(f"a{i}", "tester", "r")
        results = svc.find()
        assert len(results) == 3

    def test_verify_chain_false_when_tampered(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET hash = 'tampered' WHERE rowid = 1")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_action(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET action = 'tampered' WHERE rowid = 2")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_actor(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET actor = 'tampered' WHERE rowid = 2")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_resource(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET resource = 'tampered' WHERE rowid = 2")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_detail(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r", "original")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET detail = 'tampered' WHERE rowid = 1")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_timestamp(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET timestamp = '2020-01-01T00:00:00+00:00' WHERE rowid = 2")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_previous_hash(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        conn.execute("UPDATE audit_log SET previous_hash = 'tampered' WHERE rowid = 2")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_single_entry_mutated_hash(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        conn.execute("UPDATE audit_log SET hash = 'tampered' WHERE rowid = 1")
        conn.commit()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_broken_link_with_valid_hash(self, conn) -> None:
        svc = SQLiteAuditService(conn)
        svc.record("a1", "tester", "r")
        svc.record("a2", "tester", "r")
        row = conn.execute("SELECT * FROM audit_log WHERE rowid = 2").fetchone()
        rehashed = compute_audit_hash(
            entry_id=row["id"],
            action=row["action"],
            actor=row["actor"],
            resource=row["resource"],
            detail=row["detail"],
            timestamp_iso=row["timestamp"],
            previous_hash="tampered",
        )
        conn.execute(
            "UPDATE audit_log SET previous_hash = 'tampered', hash = ? WHERE rowid = 2",
            (rehashed,),
        )
        conn.commit()
        assert svc.verify_chain() is False


class TestSQLiteMetricsService:
    def test_counter_increments(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        assert svc.counter("trades") == 1.0
        assert svc.counter("trades") == 2.0

    def test_gauge_sets_value(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        svc.gauge("cpu", 42.5)
        assert svc.get_gauge("cpu") == 42.5

    def test_snapshot(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        svc.counter("trades", 5)
        svc.gauge("cpu", 50.0)
        snap = svc.snapshot()
        assert snap["trades"] == 5.0
        assert snap["cpu"] == 50.0

    def test_get_counter_default(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        assert svc.get_counter("nonexistent") == 0.0

    def test_get_gauge_none(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        assert svc.get_gauge("nonexistent") is None

    def test_timing_returns_context(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        with svc.timing("api.latency") as tc:
            assert tc.name == "api.latency"
        assert svc.get_gauge("api.latency") is not None

    def test_query_returns_samples(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        svc.counter("test_metric", 10)
        samples = svc.query("test_metric")
        assert len(samples) >= 1
        assert samples[0].value == 10.0

    def test_clear_metrics(self, conn) -> None:
        svc = SQLiteMetricsService(conn)
        svc.counter("trades", 5)
        svc.gauge("cpu", 50.0)
        svc.clear()
        assert svc.get_counter("trades") == 0.0
        assert svc.get_gauge("cpu") is None
        assert len(svc.query("trades")) == 0


class TestTimingContext:
    def test_timing_context_manager(self, conn) -> None:
        metrics = SQLiteMetricsService(conn)
        with TimingContext(metrics, "api.latency") as tc:
            assert tc.start is not None
        assert metrics.get_gauge("api.latency") is not None

    def test_postgres_timing_stop_without_start_returns_zero(self) -> None:
        from unittest.mock import MagicMock

        tc = PostgresTimingContext(MagicMock(), "api.latency")
        assert tc.stop() == 0.0

    def test_timing_stop_returns_elapsed(self, conn) -> None:
        metrics = SQLiteMetricsService(conn)
        tc = TimingContext(metrics, "custom")
        tc.__enter__()
        elapsed = tc.stop()
        assert elapsed > 0

    def test_stop_without_start_returns_zero(self) -> None:
        class FakeMetrics:
            def gauge(self, name: str, value: float) -> None:
                pass

        tc = TimingContext(FakeMetrics(), "test")
        assert tc.stop() == 0.0


class TestSQLiteHealthService:
    def test_register_and_get_status(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        svc.register("engine")
        assert svc.get_status("engine") is True

    def test_report_healthy(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        s = svc.report_healthy("engine", "all good")
        assert s.healthy is True
        assert s.message == "all good"

    def test_report_unhealthy(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        s = svc.report_unhealthy("engine", "timeout")
        assert s.healthy is False
        assert s.message == "timeout"

    def test_all_healthy_when_all_good(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        svc.register("a")
        svc.register("b")
        assert svc.all_healthy() is True

    def test_all_healthy_when_one_down(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        svc.register("a")
        svc.report_unhealthy("b")
        assert svc.all_healthy() is False

    def test_all_healthy_empty(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        assert svc.all_healthy() is True

    def test_summary(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        svc.register("a")
        svc.report_unhealthy("b")
        summary = svc.summary()
        assert summary["a"] is True
        assert summary["b"] is False

    def test_check_passes(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        s = svc.check("engine", lambda: True)
        assert s.healthy is True

    def test_check_fails(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        s = svc.check("engine", lambda: False)
        assert s.healthy is False

    def test_check_raises(self, conn) -> None:
        svc = SQLiteHealthService(conn)

        def failing() -> bool:
            raise RuntimeError("db down")

        s = svc.check("engine", failing)
        assert s.healthy is False
        assert "db down" in s.message

    def test_history(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        svc.report_healthy("engine")
        svc.report_unhealthy("engine")
        h = svc.history(limit=5)
        assert len(h) >= 2
        assert h[0].healthy is False
        assert h[1].healthy is True

    def test_get_status_nonexistent(self, conn) -> None:
        svc = SQLiteHealthService(conn)
        assert svc.get_status("nonexistent") is None


class TestSQLiteManifestService:
    def test_record_with_metadata(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        e = svc.record("worker", "process", "completed", 150.5, {"key": "value"})
        assert e.service == "worker"
        assert e.action == "process"
        assert e.duration_ms == 150.5
        assert e.metadata["key"] == "value"

    def test_record_defaults(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        e = svc.record("worker", "start")
        assert e.status == "completed"
        assert e.metadata == {}

    def test_get_runs_all(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        svc.record("svc1", "start")
        svc.record("svc2", "start")
        runs = svc.get_runs()
        assert len(runs) >= 2

    def test_get_runs_by_service(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        svc.record("svc1", "start")
        svc.record("svc2", "start")
        svc.record("svc1", "stop")
        runs = svc.get_runs(service="svc1")
        assert len(runs) == 2

    def test_summary(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        svc.record("svc1", "start")
        svc.record("svc1", "stop")
        svc.record("svc2", "start")
        summary = svc.summary()
        assert summary["svc1"] == 2
        assert summary["svc2"] == 1

    def test_clear(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        svc.record("svc1", "start")
        svc.clear()
        assert len(svc.get_runs()) == 0

    def test_record_with_none_metadata(self, conn) -> None:
        svc = SQLiteManifestService(conn)
        e = svc.record("worker", "start", metadata=None)
        assert e.metadata == {}
