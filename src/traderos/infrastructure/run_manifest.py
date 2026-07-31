from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.ports import ManifestEntry
from traderos.domain.ports import ManifestPort


@dataclass
class RunManifestService(ManifestPort):
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


class DurableRunManifest(ManifestPort):
    """SQLite-backed manifest so a restarted daemon can detect a crash.

    The daemon records ``start`` on boot and ``stop`` on clean shutdown. If the
    most recent action for a service is ``start`` with no following ``stop``,
    the previous process died before finishing, and ``detect_unclean_shutdown``
    reports it so the controller can run post-crash reconciliation (OT-002).
    """

    def __init__(self, conn: Any | None = None) -> None:
        from traderos.infrastructure.config.config_loader import Config
        from traderos.infrastructure.database.connection import get_connection

        self.conn = conn if conn is not None else get_connection(Config.load())
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS run_manifest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT NOT NULL
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_manifest_service " "ON run_manifest (service, id)"
        )
        self.conn.commit()

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
        self.conn.execute(
            "INSERT INTO run_manifest "
            "(run_id, service, action, status, duration_ms, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(entry.run_id),
                entry.service,
                entry.action,
                entry.status,
                entry.duration_ms,
                entry.timestamp.isoformat(),
                json.dumps(entry.metadata, sort_keys=True),
            ),
        )
        self.conn.commit()
        return entry

    def get_runs(
        self,
        service: str | None = None,
        limit: int = 100,
    ) -> list[ManifestEntry]:
        if service is None:
            rows = self.conn.execute(
                "SELECT run_id, service, action, status, duration_ms, timestamp, metadata "
                "FROM run_manifest ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT run_id, service, action, status, duration_ms, timestamp, metadata "
                "FROM run_manifest WHERE service = ? ORDER BY id DESC LIMIT ?",
                (service, limit),
            ).fetchall()
        return [self._row_to_entry(row) for row in reversed(rows)]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT service, COUNT(*) AS n FROM run_manifest GROUP BY service"
        ).fetchall():
            counts[row["service"]] = row["n"]
        return counts

    def clear(self) -> None:
        self.conn.execute("DELETE FROM run_manifest")
        self.conn.commit()

    def detect_unclean_shutdown(self, service: str, lookback: int = 50) -> bool:
        """True if the latest action for ``service`` is ``start`` (no ``stop``)."""
        runs = self.get_runs(service=service, limit=lookback)
        if not runs:
            return False
        last = runs[-1]
        return last.action == "start" and last.status in ("completed", "running")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001, S110 — best-effort teardown
            pass

    @staticmethod
    def _row_to_entry(row: Any) -> ManifestEntry:
        return ManifestEntry(
            run_id=uuid.UUID(str(row["run_id"])),
            service=str(row["service"]),
            action=str(row["action"]),
            status=str(row["status"]),
            duration_ms=float(row["duration_ms"]),
            timestamp=datetime.fromisoformat(str(row["timestamp"])),
            metadata=json.loads(str(row["metadata"] or "{}")),
        )
