from __future__ import annotations

import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from traderos.infrastructure.archiver import purge_old_entries
from traderos.infrastructure.database.migration_manager import migrate


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test.db")
    migrate(conn)
    now = datetime.now(UTC)
    old = (now - timedelta(days=120)).isoformat()
    recent = now.isoformat()
    conn.execute(
        "INSERT INTO audit_log (id, action, actor, resource, timestamp, previous_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("audit-old", "act", "system", "res", old, "0" * 64, "a" * 64),
    )
    conn.execute(
        "INSERT INTO audit_log (id, action, actor, resource, timestamp, previous_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("audit-new", "act", "system", "res", recent, "a" * 64, "b" * 64),
    )
    conn.execute(
        "INSERT INTO order_events (id, trade_id, status, payload, published, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("oe-old", "t1", "applied", "{}", 1, old),
    )
    conn.execute(
        "INSERT INTO order_events (id, trade_id, status, payload, published, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("oe-new", "t2", "applied", "{}", 1, recent),
    )
    conn.commit()
    return conn


class TestPurgeOldEntries:
    def test_purges_expired_rows(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = purge_old_entries(conn, retention_days=90)
        assert "audit_log" in result
        assert "order_events" in result
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id FROM audit_log").fetchall()
        assert {r["id"] for r in rows} == {"audit-new"}
        rows = conn.execute("SELECT id FROM order_events").fetchall()
        assert {r["id"] for r in rows} == {"oe-new"}
        conn.close()

    def test_respects_recent_rows(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        result = purge_old_entries(conn, retention_days=365)
        assert result == {}
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT COUNT(*) AS n FROM order_events").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 2
        conn.close()
