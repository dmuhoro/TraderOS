from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import NamedTuple
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class DatabasePort(Protocol):
    conn: Any

    def close(self) -> None: ...


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: dict[str, Any]
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


EventHandler = Callable[["Event"], None]


@runtime_checkable
class EventBusPort(Protocol):
    def publish(self, event: Event) -> None: ...
    def subscribe(self, event_type: str, handler: EventHandler) -> None: ...
    def unsubscribe(self, event_type: str, handler: EventHandler) -> None: ...


class HealthStatus(NamedTuple):
    service: str
    healthy: bool
    message: str
    latency_ms: float
    last_check: datetime


@runtime_checkable
class HealthPort(Protocol):
    def register(self, name: str, initial: bool = True) -> None: ...
    def report_healthy(self, name: str, message: str = "ok") -> HealthStatus: ...
    def report_unhealthy(self, name: str, message: str = "unhealthy") -> HealthStatus: ...
    def check(self, name: str, check_fn: Any) -> HealthStatus: ...
    def get_status(self, name: str) -> bool | None: ...
    def all_healthy(self) -> bool: ...
    def summary(self) -> dict[str, bool]: ...
    def history(self, limit: int = 10) -> list[HealthStatus]: ...


class AuditEntry(NamedTuple):
    id: uuid.UUID
    action: str
    actor: str
    resource: str
    detail: str
    timestamp: datetime
    previous_hash: str
    hash: str


@runtime_checkable
class AuditPort(Protocol):
    def record(self, action: str, actor: str, resource: str, detail: str = "") -> AuditEntry: ...
    def get_entries(self, limit: int = 100, offset: int = 0) -> list[AuditEntry]: ...
    def verify_chain(self) -> bool: ...
    def find(self, action: str | None = None, actor: str | None = None) -> list[AuditEntry]: ...


class MetricSample(NamedTuple):
    name: str
    value: float
    timestamp: datetime
    tags: dict[str, str]


@runtime_checkable
class MetricsPort(Protocol):
    def counter(self, name: str, delta: float = 1.0) -> float: ...
    def gauge(self, name: str, value: float) -> None: ...
    def timing(self, name: str) -> Any: ...
    def get_counter(self, name: str) -> float: ...
    def get_gauge(self, name: str) -> float | None: ...
    def snapshot(self) -> dict[str, float]: ...
    def query(self, name: str, limit: int = 100) -> list[MetricSample]: ...
    def clear(self) -> None: ...


class ManifestEntry(NamedTuple):
    run_id: uuid.UUID
    service: str
    action: str
    status: str
    duration_ms: float
    timestamp: datetime
    metadata: dict[str, str | float | int | None]


@runtime_checkable
class ManifestPort(Protocol):
    def record(
        self,
        service: str,
        action: str,
        status: str = "completed",
        duration_ms: float = 0.0,
        metadata: dict[str, str | float | int | None] | None = None,
    ) -> ManifestEntry: ...
    def get_runs(self, service: str | None = None, limit: int = 100) -> list[ManifestEntry]: ...
    def summary(self) -> dict[str, int]: ...
    def clear(self) -> None: ...
