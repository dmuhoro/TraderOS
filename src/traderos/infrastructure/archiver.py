from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any


def purge_old_entries(conn: Any, retention_days: int = 90) -> dict[str, int]:
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    results: dict[str, int] = {}
    tables = [
        ("audit_log", "timestamp"),
        ("metrics_history", "timestamp"),
        ("health_history", "timestamp"),
        ("run_manifest", "timestamp"),
        ("market_data", "timestamp"),
        ("order_events", "applied_at"),
    ]
    is_pg = hasattr(conn, "cursor") and not hasattr(conn, "row_factory")
    for table, ts_column in tables:
        try:
            if is_pg:
                with conn.cursor() as cur:
                    cur.execute(f"DELETE FROM {table} WHERE {ts_column} < %s", (cutoff,))
                    deleted = cur.rowcount
            else:
                cur = conn.execute(f"DELETE FROM {table} WHERE {ts_column} < ?", (cutoff,))
                deleted = cur.rowcount
            if deleted and deleted > 0:
                results[table] = deleted
        except Exception:
            pass
    if results:
        conn.commit()
    return results
