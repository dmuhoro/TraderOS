from __future__ import annotations

from traderos.infrastructure.metrics import MetricsService


class TestMetricsService:
    def test_counter_default(self) -> None:
        svc = MetricsService()
        assert svc.get_counter("requests") == 0.0

    def test_counter_increment(self) -> None:
        svc = MetricsService()
        svc.counter("requests")
        assert svc.get_counter("requests") == 1.0

    def test_counter_multiple(self) -> None:
        svc = MetricsService()
        svc.counter("requests", 5.0)
        svc.counter("requests", 3.0)
        assert svc.get_counter("requests") == 8.0

    def test_gauge(self) -> None:
        svc = MetricsService()
        svc.gauge("cpu", 0.85)
        assert svc.get_gauge("cpu") == 0.85

    def test_gauge_overwrite(self) -> None:
        svc = MetricsService()
        svc.gauge("cpu", 0.85)
        svc.gauge("cpu", 0.90)
        assert svc.get_gauge("cpu") == 0.90

    def test_gauge_nonexistent(self) -> None:
        svc = MetricsService()
        assert svc.get_gauge("nonexistent") is None

    def test_snapshot(self) -> None:
        svc = MetricsService()
        svc.counter("orders", 10)
        svc.gauge("cpu", 0.5)
        snap = svc.snapshot()
        assert snap["orders"] == 10.0
        assert snap["cpu"] == 0.5

    def test_query(self) -> None:
        svc = MetricsService()
        svc.counter("test")
        svc.counter("other")
        assert len(svc.query("test")) == 1

    def test_query_limit(self) -> None:
        svc = MetricsService()
        for _ in range(10):
            svc.counter("x")
        assert len(svc.query("x", limit=5)) == 5

    def test_clear(self) -> None:
        svc = MetricsService()
        svc.counter("x", 5)
        svc.clear()
        assert svc.get_counter("x") == 0.0

    def test_timing_context(self) -> None:
        svc = MetricsService()
        with svc.timing("api_latency") as t:
            pass
        elapsed = t.stop()
        assert elapsed >= 0
        assert svc.get_gauge("api_latency") is not None
