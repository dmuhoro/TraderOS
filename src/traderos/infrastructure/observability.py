from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Self

from traderos.domain.ports import AuditEntry
from traderos.domain.ports import AuditPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import HealthStatus
from traderos.domain.ports import ManifestEntry
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricSample
from traderos.domain.ports import MetricsPort
from traderos.infrastructure.audit import compute_audit_hash
from traderos.infrastructure.health import DEFAULT_CHECK_TIMEOUT
from traderos.infrastructure.health import run_with_timeout


class SQLiteAuditService(AuditPort):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _get_previous_hash(self) -> str:
        row = self.conn.execute("SELECT hash FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else "genesis"

    def record(self, action: str, actor: str, resource: str, detail: str = "") -> AuditEntry:
        prev_hash = self._get_previous_hash()
        entry_id = uuid.uuid4()
        ts = datetime.now(UTC)
        raw = AuditEntry(
            id=entry_id,
            action=action,
            actor=actor,
            resource=resource,
            detail=detail,
            timestamp=ts,
            previous_hash=prev_hash,
            hash="",
        )
        h = compute_audit_hash(
            entry_id=str(raw.id),
            action=raw.action,
            actor=raw.actor,
            resource=raw.resource,
            detail=raw.detail,
            timestamp_iso=raw.timestamp.isoformat(),
            previous_hash=raw.previous_hash,
        )
        entry = raw._replace(hash=h)
        self.conn.execute(
            "INSERT INTO audit_log (id, action, actor, resource, detail,"
            " timestamp, previous_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(entry.id),
                entry.action,
                entry.actor,
                entry.resource,
                entry.detail,
                entry.timestamp.isoformat(),
                entry.previous_hash,
                entry.hash,
            ),
        )
        self.conn.commit()
        return entry

    def get_entries(self, limit: int = 100, offset: int = 0) -> list[AuditEntry]:
        cursor = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [
            AuditEntry(
                id=uuid.UUID(r["id"]),
                action=r["action"],
                actor=r["actor"],
                resource=r["resource"],
                detail=r["detail"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                previous_hash=r["previous_hash"],
                hash=r["hash"],
            )
            for r in cursor.fetchall()
        ]

    def verify_chain(self) -> bool:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY rowid").fetchall()
        for i, row in enumerate(rows):
            expected_hash = compute_audit_hash(
                entry_id=row["id"],
                action=row["action"],
                actor=row["actor"],
                resource=row["resource"],
                detail=row["detail"],
                timestamp_iso=row["timestamp"],
                previous_hash=row["previous_hash"],
            )
            if row["hash"] != expected_hash:
                return False
            if i > 0 and row["previous_hash"] != rows[i - 1]["hash"]:
                return False
        return True

    def find(self, action: str | None = None, actor: str | None = None) -> list[AuditEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        where = " AND ".join(clauses) if clauses else "1=1"
        cursor = self.conn.execute(f"SELECT * FROM audit_log WHERE {where} ORDER BY rowid", params)
        return [
            AuditEntry(
                id=uuid.UUID(r["id"]),
                action=r["action"],
                actor=r["actor"],
                resource=r["resource"],
                detail=r["detail"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                previous_hash=r["previous_hash"],
                hash=r["hash"],
            )
            for r in cursor.fetchall()
        ]


class SQLiteMetricsService(MetricsPort):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}

    def counter(self, name: str, delta: float = 1.0) -> float:
        val = self._counters.get(name, 0.0) + delta
        self._counters[name] = val
        self._save(name, val)
        return val

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value
        self._save(name, value)

    def _save(self, name: str, value: float) -> None:
        self.conn.execute(
            "INSERT INTO metrics_history (name, value, timestamp) VALUES (?, ?, ?)",
            (name, value, datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

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
        cursor = self.conn.execute(
            "SELECT * FROM metrics_history WHERE name = ?" " ORDER BY rowid DESC LIMIT ?",
            (name, limit),
        )
        return [
            MetricSample(
                name=r["name"],
                value=r["value"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                tags=json.loads(r["tags"]) if r["tags"] else {},
            )
            for r in cursor.fetchall()
        ]

    def clear(self) -> None:
        self._counters.clear()
        self._gauges.clear()
        self.conn.execute("DELETE FROM metrics_history")
        self.conn.commit()


class TimingContext:
    def __init__(self, metrics: MetricsPort, name: str) -> None:
        self.metrics = metrics
        self.name = name
        self.start = None

    def __enter__(self) -> Self:
        import time

        self.start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        import time

        if self.start is not None:
            elapsed = (time.perf_counter() - self.start) * 1000
            self.metrics.gauge(self.name, elapsed)

    def stop(self) -> float:
        import time

        if self.start is not None:
            elapsed = (time.perf_counter() - self.start) * 1000
            self.metrics.gauge(self.name, elapsed)
            return elapsed
        return 0.0


class SQLiteHealthService(HealthPort):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.services: dict[str, bool] = {}

    def register(self, name: str, initial: bool = True) -> None:
        self.services[name] = initial

    def report_healthy(self, name: str, message: str = "ok") -> HealthStatus:
        self.services[name] = True
        s = HealthStatus(
            service=name,
            healthy=True,
            message=message,
            latency_ms=0.0,
            last_check=datetime.now(UTC),
        )
        self._save(s)
        return s

    def report_unhealthy(self, name: str, message: str = "unhealthy") -> HealthStatus:
        self.services[name] = False
        s = HealthStatus(
            service=name,
            healthy=False,
            message=message,
            latency_ms=0.0,
            last_check=datetime.now(UTC),
        )
        self._save(s)
        return s

    def _save(self, status: HealthStatus) -> None:
        self.conn.execute(
            "INSERT INTO health_history (service, healthy, message,"
            " latency_ms, timestamp) VALUES (?, ?, ?, ?, ?)",
            (
                status.service,
                int(status.healthy),
                status.message,
                status.latency_ms,
                status.last_check.isoformat(),
            ),
        )
        self.conn.commit()

    def check(self, name: str, check_fn: Any) -> HealthStatus:
        try:
            if bool(run_with_timeout(check_fn, DEFAULT_CHECK_TIMEOUT)):
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
        cursor = self.conn.execute(
            "SELECT * FROM health_history ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )
        return [
            HealthStatus(
                service=r["service"],
                healthy=bool(r["healthy"]),
                message=r["message"],
                latency_ms=r["latency_ms"],
                last_check=datetime.fromisoformat(r["timestamp"]),
            )
            for r in cursor.fetchall()
        ]


class SQLiteManifestService(ManifestPort):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

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
            "INSERT INTO run_manifest (run_id, service, action, status,"
            " duration_ms, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(entry.run_id),
                entry.service,
                entry.action,
                entry.status,
                entry.duration_ms,
                entry.timestamp.isoformat(),
                json.dumps(entry.metadata, default=str),
            ),
        )
        self.conn.commit()
        return entry

    def get_runs(self, service: str | None = None, limit: int = 100) -> list[ManifestEntry]:
        if service:
            cursor = self.conn.execute(
                "SELECT * FROM run_manifest WHERE service = ?" " ORDER BY rowid DESC LIMIT ?",
                (service, limit),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM run_manifest ORDER BY rowid DESC LIMIT ?",
                (limit,),
            )
        return [
            ManifestEntry(
                run_id=uuid.UUID(r["run_id"]),
                service=r["service"],
                action=r["action"],
                status=r["status"],
                duration_ms=r["duration_ms"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
            )
            for r in cursor.fetchall()
        ]

    def summary(self) -> dict[str, int]:
        cursor = self.conn.execute(
            "SELECT service, COUNT(*) as cnt FROM run_manifest GROUP BY service"
        )
        return {r["service"]: r["cnt"] for r in cursor.fetchall()}

    def clear(self) -> None:
        self.conn.execute("DELETE FROM run_manifest")
        self.conn.commit()
