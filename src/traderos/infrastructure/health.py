from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import TypeVar

from traderos.domain.ports import HealthPort
from traderos.domain.ports import HealthStatus

DEFAULT_CHECK_TIMEOUT = 5.0

_T = TypeVar("_T")


def run_with_timeout(check_fn: Callable[[], _T], timeout: float) -> _T:
    """Run a check with a hard wall-clock bound (OT-010).

    A hung dependency must mark the service unhealthy instead of stalling the
    caller. The worker is a daemon thread so a genuinely stuck check can never
    block process shutdown. The check may return any value (a health ``bool``
    or, for API readiness, the initialized orchestrator).
    """
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            result["value"] = check_fn()
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=_run, name="health-check", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"health check exceeded {timeout}s")
    if "error" in result:
        raise result["error"]
    return result["value"]


@dataclass
class HealthService(HealthPort):
    services: dict[str, bool] = field(default_factory=dict)
    _history: list[HealthStatus] = field(default_factory=list)
    check_timeout: float = DEFAULT_CHECK_TIMEOUT

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

    def report_unhealthy(self, name: str, message: str = "unhealthy") -> HealthStatus:
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

    def check(self, name: str, check_fn: Callable[[], bool]) -> HealthStatus:
        try:
            if bool(run_with_timeout(check_fn, self.check_timeout)):
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
