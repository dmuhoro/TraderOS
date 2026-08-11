from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from traderos.infrastructure.audit import compute_audit_hash
from traderos.infrastructure.observability_postgres import PostgresAuditService

DSN = os.environ.get(
    "POSTGRES_TEST_DSN",
    "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
)


def _pg_reachable(dsn: str, timeout: int = 3) -> bool:
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        conn.close()
        return True
    except psycopg2.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(DSN),
    reason=f"Postgres not reachable at {DSN} — skipped, not passed",
)


@pytest.fixture
def pg_conn():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS audit_log CASCADE")
        cur.execute("""
            CREATE TABLE audit_log (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                resource TEXT NOT NULL,
                detail TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                id_seq SERIAL NOT NULL
            )
        """)
    yield conn
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    conn.close()


def _insert(
    conn, action: str, actor: str, resource: str, detail: str = "", previous_hash: str = "genesis"
) -> dict:
    entry_id = str(uuid.uuid4())
    ts = "2026-07-30T12:00:00+00:00"
    h = compute_audit_hash(
        entry_id=entry_id,
        action=action,
        actor=actor,
        resource=resource,
        detail=detail,
        timestamp_iso=ts,
        previous_hash=previous_hash,
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (id, action, actor, resource, detail,"
            " timestamp, previous_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (entry_id, action, actor, resource, detail, ts, previous_hash, h),
        )
    return {
        "id": entry_id,
        "action": action,
        "actor": actor,
        "resource": resource,
        "detail": detail,
        "timestamp": ts,
        "previous_hash": previous_hash,
        "hash": h,
    }


def _debug_chain(conn) -> list[dict]:
    """Debug helper: return each row's hash and link comparison for diagnosis."""
    results = []
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM audit_log ORDER BY id_seq")
        rows = cur.fetchall()
    cols = [
        "id",
        "action",
        "actor",
        "resource",
        "detail",
        "timestamp",
        "previous_hash",
        "hash",
        "id_seq",
    ]
    for i, row in enumerate(rows):
        d = dict(zip(cols, row, strict=False))
        expected = compute_audit_hash(
            entry_id=d["id"],
            action=d["action"],
            actor=d["actor"],
            resource=d["resource"],
            detail=d["detail"],
            timestamp_iso=d["timestamp"],
            previous_hash=d["previous_hash"],
        )
        match = d["hash"] == expected
        link_ok = True
        if i > 0:
            prev = dict(zip(cols, rows[i - 1], strict=False))
            link_ok = d["previous_hash"] == prev["hash"]
        results.append(
            {
                "index": i,
                "stored": d["hash"],
                "expected": expected,
                "match": match,
                "prev_hash_field": d["previous_hash"] if i > 0 else "N/A",
                "prev_row_hash": prev["hash"] if i > 0 else "N/A",
                "link_ok": link_ok,
            }
        )
    return results


class TestPostgresAuditServiceChain:
    def _verify_with_fresh_conn(self) -> PostgresAuditService:
        """Create a fresh connection for verification to avoid cursor visibility races."""
        fresh = psycopg2.connect(DSN)
        fresh.autocommit = True
        return PostgresAuditService(fresh)

    def test_verify_chain_passes_with_untampered_entries(self, pg_conn):
        e1 = _insert(pg_conn, "login", "alice", "system")
        _insert(pg_conn, "trade", "bob", "BTC/USD", "buy 0.1", previous_hash=e1["hash"])
        svc = self._verify_with_fresh_conn()
        result = svc.verify_chain()
        if not result:
            debug = _debug_chain(pg_conn)
            pytest.fail(f"verify_chain returned False. Debug: {debug}")

    def test_verify_chain_detects_mutated_action(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system")
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET action = 'tampered' WHERE id = %s", (entry["id"],))
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_actor(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system")
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET actor = 'tampered' WHERE id = %s", (entry["id"],))
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_resource(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system")
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET resource = 'tampered' WHERE id = %s", (entry["id"],))
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_detail(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system", "original")
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET detail = 'tampered' WHERE id = %s", (entry["id"],))
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_timestamp(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system")
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE audit_log SET timestamp = '2020-01-01T00:00:00+00:00' WHERE id = %s",
                (entry["id"],),
            )
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_mutated_previous_hash(self, pg_conn):
        entry = _insert(pg_conn, "login", "alice", "system")
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE audit_log SET previous_hash = 'tampered' WHERE id = %s",
                (entry["id"],),
            )
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False

    def test_verify_chain_detects_broken_link(self, pg_conn):
        _insert(pg_conn, "login", "alice", "system")
        _insert(pg_conn, "trade", "bob", "BTC/USD", previous_hash="tampered-link")
        svc = self._verify_with_fresh_conn()
        assert svc.verify_chain() is False
