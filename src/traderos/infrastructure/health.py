from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import NamedTuple


class HealthStatus(NamedTuple):
    service: str
    healthy: bool
    message: str
    latency_ms: float
    last_check: datetime


@dataclass
class HealthService:
    services: dict[str, bool] = field(default_factory=dict)
    _history: list[HealthStatus] = field(default_factory=list)

    def register(self, name: str, initial: bool = True) -> None:
        self.services[name] = initial

    def report_healthy(self, name: str, message: str = "ok") -> HealthStatus:
        self.services[name] = True
        status = HealthStatus(
            service=name,
            healthy=True,
            message=message,
            latency_ms=0.0,
            last_check=datetime.now(UTC),
        )
        self._history.append(status)
        return status

    def report_unhealthy(
        self, name: str, message: str = "unhealthy"
    ) -> HealthStatus:
        self.services[name] = False
        status = HealthStatus(
            service=name,
            healthy=False,
            message=message,
            latency_ms=0.0,
            last_check=datetime.now(UTC),
        )
        self._history.append(status)
        return status

    def check(self, name: str, check_fn) -> HealthStatus:
        try:
            result = check_fn()
            if result:
                return self.report_healthy(name, "check passed")
            return self.report_unhealthy(name, "check failed")
        except (RuntimeError, ValueError, OSError) as e:
            return self.report_unhealthy(name, str(e))

    def get_status(self, name: str) -> bool | None:
        return self.services.get(name)

    def all_healthy(self) -> bool:
        return all(self.services.values()) if self.services else True

    def summary(self) -> dict[str, bool]:
        return dict(self.services)

    def history(self, limit: int = 10) -> list[HealthStatus]:
        return self._history[-limit:]
