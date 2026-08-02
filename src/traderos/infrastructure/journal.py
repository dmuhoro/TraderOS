"""Durable order-event journal.

Backs the order lifecycle with:
- durable idempotency keys (survive process restart, OT-002),
- an outbox that keeps events until publishing succeeds (OT-003),
- a replay path that republishes unpublished events after restart (OT-002).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.ports import Event

_TABLE = "order_events"


class OrderEventJournal:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{{}}',
                published INTEGER NOT NULL DEFAULT 0,
                applied_at TEXT NOT NULL
            )
        """)
        self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_trade_id ON {_TABLE}(trade_id)")
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_published ON {_TABLE}(published)"
        )
        self.conn.commit()

    def contains(self, event_id: str) -> bool:
        row = self.conn.execute(f"SELECT 1 FROM {_TABLE} WHERE id = ?", (event_id,)).fetchone()
        return row is not None

    def load_event_ids(self) -> set[str]:
        rows = self.conn.execute(f"SELECT id FROM {_TABLE}").fetchall()
        return {r["id"] for r in rows}

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Return ``{status, payload}`` for a recorded event, else ``None``."""
        row = self.conn.execute(
            f"SELECT status, payload FROM {_TABLE} WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return {"status": row["status"], "payload": json.loads(row["payload"])}

    def update(self, event_id: str, status: str, payload: dict[str, Any]) -> None:
        """Update an existing durable record (durable intent confirmations)."""
        self.conn.execute(
            f"UPDATE {_TABLE} SET status = ?, payload = ? WHERE id = ?",
            (status, json.dumps(payload, default=str), event_id),
        )
        self.conn.commit()

    def record(self, event_id: str, trade_id: str, status: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            f"INSERT INTO {_TABLE} (id, trade_id, status, payload, published, applied_at)"
            " VALUES (?, ?, ?, ?, 0, ?)",
            (
                event_id,
                trade_id,
                status,
                json.dumps(payload, default=str),
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        self.conn.commit()

    def mark_published(self, event_id: str) -> None:
        self.conn.execute(f"UPDATE {_TABLE} SET published = 1 WHERE id = ?", (event_id,))
        self.conn.commit()

    def pending_events(self) -> list[tuple[str, str, dict[str, Any]]]:
        rows = self.conn.execute(
            f"SELECT id, status, payload FROM {_TABLE} WHERE published = 0 ORDER BY rowid"
        ).fetchall()
        return [(r["id"], r["status"], json.loads(r["payload"])) for r in rows]

    def count(self) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()
        return int(row["n"])

    def pending_count(self) -> int:
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE published = 0"
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def encode(event: Event) -> dict[str, Any]:
        """Serialize an Event into the journal payload envelope."""
        return {
            "event_type": event.event_type,
            "payload": event.payload,
            "correlation_id": event.correlation_id,
            "trace_id": event.trace_id,
            "market": event.market,
            "strategy": event.strategy,
            "execution_context": event.execution_context,
            "timestamp": event.timestamp.isoformat(),
        }

    @staticmethod
    def decode(envelope: dict[str, Any]) -> Event:
        from datetime import datetime

        return Event(
            str(envelope["event_type"]),
            dict(envelope["payload"]),
            timestamp=datetime.fromisoformat(envelope.get("timestamp", "")),
            correlation_id=str(envelope.get("correlation_id", "")),
            trace_id=str(envelope.get("trace_id", "")),
            market=str(envelope.get("market", "")),
            strategy=str(envelope.get("strategy", "")),
            execution_context=dict(envelope.get("execution_context", {})),
        )
