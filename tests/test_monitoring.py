from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from traderos.infrastructure.monitoring import DatabaseHealthMonitor
from traderos.infrastructure.monitoring import PrometheusMetricsService
from traderos.infrastructure.monitoring import TimingContext


class TestDatabaseHealthMonitor:
    def test_check_returns_connected_with_healthy_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        monitor = DatabaseHealthMonitor()
        report = monitor.check(conn)
        assert report.connected is True
        assert report.query_latency_ms >= 0
        conn.close()

    def test_check_returns_not_connected_with_none(self):
        monitor = DatabaseHealthMonitor()
        report = monitor.check(None)
        assert report.connected is False

    def test_check_returns_not_connected_on_query_error(self):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("db unreachable")
        monitor = DatabaseHealthMonitor()
        report = monitor.check(conn)
        assert report.connected is False

    def test_check_with_pool_stats(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        mock_pool = MagicMock()
        mock_pool.stats = {"available": 5, "in_use": 2, "max": 10}
        monitor = DatabaseHealthMonitor()
        report = monitor.check(conn, pool=mock_pool)
        assert report.pool_available == 5
        assert report.pool_in_use == 2
        assert report.pool_max == 10
        conn.close()

    def test_check_reports_schema_version(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        monitor = DatabaseHealthMonitor()
        report = monitor.check(conn)
        assert report.schema_version >= 0
        conn.close()

    def test_check_records_metrics(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        metrics = MagicMock()
        monitor = DatabaseHealthMonitor(metrics=metrics)
        monitor.check(conn)
        assert metrics.gauge.called
        conn.close()


class TestPrometheusMetricsService:
    def test_counter(self):
        svc = PrometheusMetricsService()
        val = svc.counter("test.counter", 1.0)
        assert val == 1.0
        assert svc.get_counter("test.counter") == 1.0
        val2 = svc.counter("test.counter", 2.5)
        assert val2 == 3.5

    def test_gauge(self):
        svc = PrometheusMetricsService()
        svc.gauge("test.gauge", 42.0)
        assert svc.get_gauge("test.gauge") == 42.0
        svc.gauge("test.gauge", 10.0)
        assert svc.get_gauge("test.gauge") == 10.0

    def test_snapshot(self):
        svc = PrometheusMetricsService()
        svc.counter("c1", 1.0)
        svc.gauge("g1", 99.0)
        snap = svc.snapshot()
        assert snap["c1"] == 1.0
        assert snap["g1"] == 99.0

    def test_clear(self):
        svc = PrometheusMetricsService()
        svc.counter("c1", 1.0)
        svc.gauge("g1", 99.0)
        svc.clear()
        assert svc.snapshot() == {}

    def test_query_returns_empty(self):
        svc = PrometheusMetricsService()
        assert svc.query("anything") == []

    def test_timing(self):
        svc = PrometheusMetricsService()
        with svc.timing("test.timing"):
            pass
        assert svc.get_gauge("test.timing") is not None

    def test_timing_stop_records_gauge(self):
        svc = PrometheusMetricsService()
        with svc.timing("test.stop") as entered:
            assert entered.stop() >= 0
        assert svc.get_gauge("test.stop") is not None

    def test_timing_stop_without_start_returns_zero(self):
        svc = PrometheusMetricsService()
        assert TimingContext(svc, "test.nostart").stop() == 0.0


class TestPrometheusImportFallback:
    def test_missing_client_disables_prometheus(self, monkeypatch):
        import importlib
        import sys

        mod = importlib.import_module("traderos.infrastructure.monitoring")
        monkeypatch.setitem(sys.modules, "prometheus_client", None)
        reloaded = importlib.reload(mod)
        assert reloaded._has_prometheus is False

    def test_check_with_pool_records_metrics(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        metrics = MagicMock()
        mock_pool = MagicMock()
        mock_pool.stats = {"available": 5, "in_use": 2, "max": 10}
        monitor = DatabaseHealthMonitor(metrics=metrics)
        monitor.check(conn, pool=mock_pool)
        assert metrics.gauge.called
        calls = {c.args[0] for c in metrics.gauge.call_args_list}
        assert {"db.pool.available", "db.pool.in_use", "db.pool.max"} <= calls
        conn.close()

    def test_check_schema_version_failure_returns_minus_one(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        monitor = DatabaseHealthMonitor()

        def _boom(*args, **kwargs):
            raise RuntimeError("schema query failed")

        monkeypatch.setattr(
            "traderos.infrastructure.database.migration_manager.get_current_version", _boom
        )
        report = monitor.check(conn)
        assert report.connected is True
        assert report.schema_version == -1
        conn.close()
