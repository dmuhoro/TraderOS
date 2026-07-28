from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from typing import Self

from traderos.domain.ports import MetricSample
from traderos.domain.ports import MetricsPort

_has_prometheus: bool
_prometheus_client: object = None
try:
    import prometheus_client as _pc

    _prometheus_client = _pc
    _has_prometheus = True
except ImportError:
    _has_prometheus = False


class PrometheusMetricsService(MetricsPort):
    def __init__(self, namespace: str = "traderos") -> None:
        self._namespace = namespace
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._prom_counters: dict[str, Any] = {}
        self._prom_gauges: dict[str, Any] = {}
        self._prom_histograms: dict[str, Any] = {}

    def _counter(self, name: str) -> Any:
        if name not in self._prom_counters and _has_prometheus:
            self._prom_counters[name] = _prometheus_client.Counter(  # type: ignore[union-attr]
                name.replace(".", "_"),
                f"Counter: {name}",
                namespace=self._namespace,
            )
        return self._prom_counters.get(name)

    def _gauge(self, name: str) -> Any:
        if name not in self._prom_gauges and _has_prometheus:
            self._prom_gauges[name] = _prometheus_client.Gauge(  # type: ignore[union-attr]
                name.replace(".", "_"),
                f"Gauge: {name}",
                namespace=self._namespace,
            )
        return self._prom_gauges.get(name)

    def _histogram(self, name: str) -> Any:
        if name not in self._prom_histograms and _has_prometheus:
            self._prom_histograms[name] = _prometheus_client.Histogram(  # type: ignore[union-attr]
                name.replace(".", "_"),
                f"Histogram: {name}",
                namespace=self._namespace,
                buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            )
        return self._prom_histograms.get(name)

    def counter(self, name: str, delta: float = 1.0) -> float:
        val = self._counters.get(name, 0.0) + delta
        self._counters[name] = val
        pc = self._counter(name)
        if pc is not None:
            pc.inc(delta)
        return val

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value
        pg = self._gauge(name)
        if pg is not None:
            pg.set(value)

    def timing(self, name: str) -> TimingContext:
        return TimingContext(self, name)

    def observe(self, name: str, value: float) -> None:
        ph = self._histogram(name)
        if ph is not None:
            ph.observe(value / 1000.0)

    def get_counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def get_gauge(self, name: str) -> float | None:
        return self._gauges.get(name)

    def snapshot(self) -> dict[str, float]:
        result: dict[str, float] = {}
        result.update(self._counters)
        result.update(self._gauges)
        return result

    def query(self, name: str, limit: int = 100) -> list[MetricSample]:
        return []

    def clear(self) -> None:
        self._counters.clear()
        self._gauges.clear()


class TimingContext:
    def __init__(self, metrics: MetricsPort, name: str) -> None:
        self.metrics = metrics
        self.name = name
        self.start = None

    def __enter__(self) -> Self:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        if self.start is not None:
            elapsed = (time.perf_counter() - self.start) * 1000
            self.metrics.gauge(self.name, elapsed)
            if isinstance(self.metrics, PrometheusMetricsService):
                self.metrics.observe(self.name, elapsed)

    def stop(self) -> float:
        if self.start is not None:
            elapsed = (time.perf_counter() - self.start) * 1000
            self.metrics.gauge(self.name, elapsed)
            if isinstance(self.metrics, PrometheusMetricsService):
                self.metrics.observe(self.name, elapsed)
            return elapsed
        return 0.0


@dataclass
class DbHealthReport:
    connected: bool
    pool_available: int = 0
    pool_in_use: int = 0
    pool_max: int = 0
    schema_version: int = 0
    query_latency_ms: float = 0.0


class DatabaseHealthMonitor:
    def __init__(self, metrics: MetricsPort | None = None) -> None:
        self._metrics = metrics

    def check(self, conn: Any, pool: Any | None = None) -> DbHealthReport:
        report = DbHealthReport(connected=False)
        if conn is None:
            return report
        try:
            start = time.perf_counter()
            cur = conn.execute("SELECT 1")
            cur.fetchone()
            report.query_latency_ms = (time.perf_counter() - start) * 1000
            report.connected = True
            if pool is not None:
                stats = pool.stats
                report.pool_available = stats["available"]
                report.pool_in_use = stats["in_use"]
                report.pool_max = stats["max"]
                if self._metrics:
                    self._metrics.gauge("db.pool.available", float(report.pool_available))
                    self._metrics.gauge("db.pool.in_use", float(report.pool_in_use))
                    self._metrics.gauge("db.pool.max", float(report.pool_max))
                    self._metrics.gauge("db.query_latency_ms", report.query_latency_ms)
        except Exception:
            report.connected = False
        try:
            from traderos.infrastructure.database.migration_manager import get_current_version

            report.schema_version = get_current_version(conn)
        except Exception:
            report.schema_version = -1
        if self._metrics:
            self._metrics.gauge("db.connected", 1.0 if report.connected else 0.0)
            self._metrics.gauge("db.schema_version", float(report.schema_version))
        return report
