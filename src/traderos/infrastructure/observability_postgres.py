from __future__ import annotations

import json
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


def _hash(val: str) -> str:
    return str(hash(val))


class PostgresAuditService(AuditPort):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def _get_previous_hash(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
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
        h = _hash(
            "|".join(
                [
                    str(raw.id),
                    raw.action,
                    raw.actor,
                    raw.resource,
                    raw.detail,
                    raw.timestamp.isoformat(),
                    raw.previous_hash,
                ]
            )
        )
        entry = raw._replace(hash=h)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (id, action, actor, resource, detail,"
                " timestamp, previous_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            rows = cur.fetchall()
        cols = ["id", "action", "actor", "resource", "detail", "timestamp", "previous_hash", "hash"]
        return [self._row_to_entry(r, cols) for r in rows]

    def _row_to_entry(self, row: Any, cols: list[str]) -> AuditEntry:
        d = dict(zip(cols, row, strict=False))
        return AuditEntry(
            id=uuid.UUID(d["id"]),
            action=d["action"],
            actor=d["actor"],
            resource=d["resource"],
            detail=d["detail"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            previous_hash=d["previous_hash"],
            hash=d["hash"],
        )

    def verify_chain(self) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM audit_log ORDER BY id")
            rows = cur.fetchall()
        for i in range(1, len(rows)):
            if rows[i][6] != rows[i - 1][7]:
                return False
        return True

    def find(self, action: str | None = None, actor: str | None = None) -> list[AuditEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = %s")
            params.append(action)
        if actor:
            clauses.append("actor = %s")
            params.append(actor)
        where = " AND ".join(clauses) if clauses else "true"
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM audit_log WHERE {where} ORDER BY id", params)
            rows = cur.fetchall()
        cols = ["id", "action", "actor", "resource", "detail", "timestamp", "previous_hash", "hash"]
        return [self._row_to_entry(r, cols) for r in rows]


class PostgresMetricsService(MetricsPort):
    def __init__(self, conn: Any) -> None:
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
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO metrics_history (name, value, timestamp) VALUES (%s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM metrics_history WHERE name = %s ORDER BY id DESC LIMIT %s",
                (name, limit),
            )
            rows = cur.fetchall()
        return [
            MetricSample(
                name=r[1],
                value=r[2],
                timestamp=datetime.fromisoformat(r[3]),
                tags=json.loads(r[4]) if r[4] else {},
            )
            for r in rows
        ]

    def clear(self) -> None:
        self._counters.clear()
        self._gauges.clear()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM metrics_history")
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


class PostgresHealthService(HealthPort):
    def __init__(self, conn: Any) -> None:
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
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_history (service, healthy, message,"
                " latency_ms, timestamp) VALUES (%s, %s, %s, %s, %s)",
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
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM health_history ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        return [
            HealthStatus(
                service=r[1],
                healthy=bool(r[2]),
                message=r[3],
                latency_ms=r[4],
                last_check=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]


class PostgresManifestService(ManifestPort):
    def __init__(self, conn: Any) -> None:
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
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO run_manifest (run_id, service, action, status,"
                " duration_ms, timestamp, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM run_manifest WHERE service = %s ORDER BY id DESC LIMIT %s",
                    (service, limit),
                )
                rows = cur.fetchall()
        else:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM run_manifest ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _row_to_entry(self, row: Any) -> ManifestEntry:
        return ManifestEntry(
            run_id=uuid.UUID(row[1]),
            service=row[2],
            action=row[3],
            status=row[4],
            duration_ms=row[5],
            timestamp=datetime.fromisoformat(row[6]),
            metadata=json.loads(row[7]) if row[7] else {},
        )

    def summary(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT service, COUNT(*) as cnt FROM run_manifest GROUP BY service")
            rows = cur.fetchall()
        return {r[0]: r[1] for r in rows}

    def clear(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM run_manifest")
        self.conn.commit()
