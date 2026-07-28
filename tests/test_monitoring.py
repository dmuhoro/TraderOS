from __future__ import annotations

from traderos.infrastructure.monitoring import PrometheusMetricsService


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
