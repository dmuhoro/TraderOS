from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from traderos.infrastructure.monitoring import DatabaseHealthMonitor
from traderos.infrastructure.monitoring import PrometheusMetricsService


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
