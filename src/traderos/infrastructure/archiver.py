from __future__ import annotations

import sqlite3
from datetime import UTC
from datetime import datetime
from datetime import timedelta


def purge_old_entries(conn: sqlite3.Connection, retention_days: int = 90) -> dict[str, int]:
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    results: dict[str, int] = {}
    tables = [
        "audit_log",
        "metrics_history",
        "health_history",
        "run_manifest",
        "market_data",
    ]
    for table in tables:
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            if deleted > 0:
                results[table] = deleted
        except sqlite3.OperationalError:
            pass
    if results:
        conn.commit()
    return results
