from __future__ import annotations

import time
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Self

from traderos.domain.ports import MetricSample
from traderos.domain.ports import MetricsPort


@dataclass
class MetricsService(MetricsPort):
    _samples: list[MetricSample] = field(default_factory=list)
    _counters: dict[str, float] = field(default_factory=dict)
    _gauges: dict[str, float] = field(default_factory=dict)

    def counter(self, name: str, delta: float = 1.0) -> float:
        self._counters[name] = self._counters.get(name, 0.0) + delta
        self._samples.append(
            MetricSample(
                name=name,
                value=self._counters[name],
                timestamp=datetime.now(UTC),
                tags={},
            )
        )
        return self._counters[name]

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value
        self._samples.append(
            MetricSample(
                name=name,
                value=value,
                timestamp=datetime.now(UTC),
                tags={},
            )
        )

    def timing(self, name: str) -> TimingContext:
        return TimingContext(self, name)

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
        matching = [s for s in self._samples if s.name == name]
        return matching[-limit:]

    def clear(self) -> None:
        self._samples.clear()
        self._counters.clear()
        self._gauges.clear()


@dataclass
class TimingContext:
    metrics: MetricsPort
    name: str
    start: float = field(default_factory=time.perf_counter)

    def stop(self) -> float:
        elapsed = (time.perf_counter() - self.start) * 1000
        self.metrics.gauge(self.name, elapsed)
        return elapsed

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
