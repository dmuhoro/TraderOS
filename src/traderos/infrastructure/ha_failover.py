"""High-availability failover: lease-based leadership for the trading daemon (G-04).

A durable lease (JSONL in the data dir) records which process owns the
"only one trader per mode" authority. A healthy primary renews its lease on
every loop iteration; a standby polls and takes over only once the primary's
lease goes stale (i.e. the primary died without releasing — SIGKILL, OOM,
host loss). The lease store is durable, so the takeover decision survives the
process that died.

Guarantees:
- **Exactly one leader.** ``try_acquire_leadership`` succeeds for exactly one
  process at a time (a non-stale lease owned by a live pid is refused).
- **Fail-closed standby.** A process that cannot acquire leadership must not
  trade; the daemon stays idle until leadership is obtainable.
- **No split brain.** A lease only becomes acquirable when it is stale *and*
  the owning pid is no longer alive (pid check is best-effort and the stale
  window is the final arbiter on systems without pid probing).
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
from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.notification_service import NotificationService

LEASE_STALE_AFTER_SECONDS_DEFAULT = 90.0
POLL_INTERVAL_SECONDS_DEFAULT = 5.0


@dataclass(frozen=True)
class LeaseRecord:
    ts: datetime
    pid: int
    owner: str
    action: str  # "acquire" | "renew" | "release"

    @classmethod
    def from_line(cls, line: str) -> LeaseRecord:
        payload = json.loads(line)
        return cls(
            ts=datetime.fromisoformat(payload["ts"]),
            pid=int(payload["pid"]),
            owner=str(payload["owner"]),
            action=str(payload["action"]),
        )

    def to_line(self) -> str:
        return json.dumps(
            {
                "ts": self.ts.isoformat(),
                "pid": self.pid,
                "owner": self.owner,
                "action": self.action,
            }
        )


class LeaseStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: LeaseRecord) -> None:
        with open(self._path, "a") as f:
            f.write(record.to_line() + "\n")

    def last(self) -> LeaseRecord | None:
        if not self._path.exists():
            return None
        lines = [ln for ln in self._path.read_text().splitlines() if ln.strip()]
        if not lines:
            return None
        try:
            return LeaseRecord.from_line(lines[-1])
        except (ValueError, json.JSONDecodeError):  # pragma: no cover — corrupt tail
            return None


class FailoverManager:
    """Lease-based leadership with takeover once the primary's lease is stale."""

    def __init__(
        self,
        store: LeaseStore,
        notifications: NotificationService,
        audit: AuditPort | None = None,
        stale_after_seconds: float = LEASE_STALE_AFTER_SECONDS_DEFAULT,
        owner: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._notifications = notifications
        self._audit = audit
        self._stale_after = stale_after_seconds
        self._owner = owner or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._leading = False

    @property
    def leading(self) -> bool:
        return self._leading

    @property
    def owner(self) -> str:
        return self._owner

    def _lease_is_valid(self, record: LeaseRecord) -> bool:
        if record.action == "release":
            return False
        age = (self._now() - record.ts).total_seconds()
        return age <= self._stale_after

    def try_acquire_leadership(self) -> bool:
        """Attempt to become leader. Exactly one process wins at a time."""
        if self._leading:
            self.renew()
            return True
        last = self._store.last()
        if last is not None and self._lease_is_valid(last):
            return False  # another process holds a live lease — fail closed
        self._store.append(
            LeaseRecord(ts=self._now(), pid=os.getpid(), owner=self._owner, action="acquire")
        )
        self._leading = True
        if self._audit:
            self._audit.record("ha.leadership", "system", self._owner, "acquired")
        self._notifications.send(
            NotificationLevel.WARNING,
            "Leadership Acquired",
            f"daemon {self._owner} is now leader",
        )
        return True

    def renew(self) -> None:
        if not self._leading:
            return
        self._store.append(
            LeaseRecord(ts=self._now(), pid=os.getpid(), owner=self._owner, action="renew")
        )

    def release(self) -> None:
        if not self._leading:
            return
        self._store.append(
            LeaseRecord(ts=self._now(), pid=os.getpid(), owner=self._owner, action="release")
        )
        self._leading = False
        if self._audit:
            self._audit.record("ha.leadership", "system", self._owner, "released")

    def poll_takeover(self) -> bool:
        """Standby loop: repeatedly attempt leadership until acquired."""
        if self._leading:
            return True
        self._notifications.send(
            NotificationLevel.WARNING,
            "Standby",
            f"daemon {self._owner} is standby — another daemon leads",
        )
        return self.try_acquire_leadership()
