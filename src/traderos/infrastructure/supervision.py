"""Process supervision: heartbeat watchdog that surfaces unclean death (G-04).

The trader must be *told* when the loop that guards their capital dies. This
service writes a durable heartbeat on every loop iteration and a clean-shutdown
marker on graceful stop. On a later start it inspects the last record: a stale
heartbeat with no shutdown marker means the previous process was killed (crash,
OOM, SIGKILL, power loss) — and a CRITICAL alert is delivered through the real
notification seam, plus an audit entry and a metric.

The store is durable (JSONL in the data dir), so the detection survives the
process that died.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.notification_service import NotificationService

STALE_AFTER_SECONDS_DEFAULT = 300.0

_HEARTBEAT_NS = uuid.UUID("7a8f1c2e-3d4b-4a6c-9f0e-5d1c2b3a4e5f")


@dataclass(frozen=True)
class HeartbeatRecord:
    ts: datetime
    pid: int
    action: str  # "heartbeat" | "shutdown"
    component: str = "daemon"

    @classmethod
    def from_line(cls, line: str) -> HeartbeatRecord:
        payload = json.loads(line)
        return cls(
            ts=datetime.fromisoformat(payload["ts"]),
            pid=int(payload["pid"]),
            action=str(payload["action"]),
            component=str(payload.get("component", "daemon")),
        )

    def to_line(self) -> str:
        return json.dumps(
            {
                "ts": self.ts.isoformat(),
                "pid": self.pid,
                "action": self.action,
                "component": self.component,
            }
        )


class JsonlHeartbeatStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: HeartbeatRecord) -> None:
        with open(self._path, "a") as f:
            f.write(record.to_line() + "\n")

    def last(self) -> HeartbeatRecord | None:
        if not self._path.exists():
            return None
        lines = [ln for ln in self._path.read_text().splitlines() if ln.strip()]
        if not lines:
            return None
        try:
            return HeartbeatRecord.from_line(lines[-1])
        except (ValueError, json.JSONDecodeError):  # pragma: no cover — corrupt tail
            return None


class SupervisionService:
    def __init__(
        self,
        store: JsonlHeartbeatStore,
        notifications: NotificationService,
        audit: AuditPort | None = None,
        metrics: MetricsPort | None = None,
        stale_after_seconds: float = STALE_AFTER_SECONDS_DEFAULT,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._notifications = notifications
        self._audit = audit
        self._metrics = metrics
        self._stale_after = stale_after_seconds
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._unclean_deaths = 0

    @property
    def unclean_deaths(self) -> int:
        return self._unclean_deaths

    def heartbeat(self, component: str = "daemon") -> None:
        self._store.append(
            HeartbeatRecord(
                ts=self._now(), pid=os.getpid(), action="heartbeat", component=component
            )
        )

    def mark_clean_shutdown(self, component: str = "daemon") -> None:
        self._store.append(
            HeartbeatRecord(ts=self._now(), pid=os.getpid(), action="shutdown", component=component)
        )

    def check_unclean_shutdown(self, component: str = "daemon") -> bool:
        """Detect a previous process that died without a clean shutdown.

        Returns True (and delivers a CRITICAL alert, audit entry and metric)
        when the last persisted record is a *stale heartbeat* — i.e. a running
        process wrote a beat and never wrote the shutdown marker.
        """
        last = self._store.last()
        if last is None:
            return False
        if last.action == "shutdown":
            return False
        age = (self._now() - last.ts).total_seconds()
        if age <= self._stale_after:
            return False
        self._unclean_deaths += 1
        msg = (
            f"previous process (pid={last.pid}) heartbeat is {age:.0f}s old with "
            "no clean shutdown — it was likely killed. Verify broker state."
        )
        self._notifications.send(
            NotificationLevel.CRITICAL,
            "Unclean Process Death",
            msg,
            metadata={"age_s": age},
        )
        if self._audit:
            self._audit.record("supervision.unclean_death", "system", component, msg)
        if self._metrics:
            self._metrics.counter("supervision.unclean_death", 1.0)
        return True
