from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime

from traderos.domain.ports import AuditEntry
from traderos.domain.ports import AuditPort


def _canonical_json(*fields: object) -> str:
    return json.dumps(fields, separators=(",", ":"), sort_keys=False, ensure_ascii=True)


def compute_audit_hash(
    entry_id: str,
    action: str,
    actor: str,
    resource: str,
    detail: str,
    timestamp_iso: str,
    previous_hash: str,
) -> str:
    canonical = _canonical_json(
        entry_id, action, actor, resource, detail, timestamp_iso, previous_hash,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_hash(entry: AuditEntry) -> str:
    return compute_audit_hash(
        entry_id=str(entry.id),
        action=entry.action,
        actor=entry.actor,
        resource=entry.resource,
        detail=entry.detail,
        timestamp_iso=entry.timestamp.isoformat(),
        previous_hash=entry.previous_hash,
    )


@dataclass
class AuditService(AuditPort):
    _entries: list[AuditEntry] = field(default_factory=list)

    def record(
        self,
        action: str,
        actor: str,
        resource: str,
        detail: str = "",
    ) -> AuditEntry:
        prev_hash = self._entries[-1].hash if self._entries else "genesis"
        entry = AuditEntry(
            id=uuid.uuid4(),
            action=action,
            actor=actor,
            resource=resource,
            detail=detail,
            timestamp=datetime.now(UTC),
            previous_hash=prev_hash,
            hash="",
        )
        entry = entry._replace(hash=_compute_hash(entry))
        self._entries.append(entry)
        return entry

    def get_entries(self, limit: int = 100, offset: int = 0) -> list[AuditEntry]:
        return self._entries[offset : offset + limit]

    def verify_chain(self) -> bool:
        for i in range(1, len(self._entries)):
            expected_prev = self._entries[i - 1].hash
            if self._entries[i].previous_hash != expected_prev:
                return False
        return True

    def find(self, action: str | None = None, actor: str | None = None) -> list[AuditEntry]:
        results = list(self._entries)
        if action:
            results = [e for e in results if e.action == action]
        if actor:
            results = [e for e in results if e.actor == actor]
        return results

    def clear(self) -> None:
        self._entries.clear()
