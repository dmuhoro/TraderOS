from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import NamedTuple


class ManifestEntry(NamedTuple):
    run_id: uuid.UUID
    service: str
    action: str
    status: str
    duration_ms: float
    timestamp: datetime
    metadata: dict[str, str | float | int | None]


@dataclass
class RunManifestService:
    _entries: list[ManifestEntry] = field(default_factory=list)

    def record(
        self,
        service: str,
        action: str,
        status: str = "completed",
        duration_ms: float = 0.0,
        metadata: dict[str, str | float | int | None] | None = None,
    ) -> ManifestEntry:
        entry = ManifestEntry(
            run_id=uuid.uuid4(),
            service=service,
            action=action,
            status=status,
            duration_ms=duration_ms,
            timestamp=datetime.now(UTC),
            metadata=metadata or {},
        )
        self._entries.append(entry)
        return entry

    def get_runs(
        self,
        service: str | None = None,
        limit: int = 100,
    ) -> list[ManifestEntry]:
        results = list(self._entries)
        if service:
            results = [e for e in results if e.service == service]
        return results[-limit:]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.service] = counts.get(e.service, 0) + 1
        return counts

    def clear(self) -> None:
        self._entries.clear()
