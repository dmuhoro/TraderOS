from traderos.infrastructure.database.migration_utils import execute

VERSION = 2
DESCRIPTION = "Observability persistence: audit_log, metrics_history, health_history, run_manifest"

PG = "postgres"


def _serial(backend: str) -> str:
    return "SERIAL PRIMARY KEY" if backend == PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def up(conn, backend: str = "sqlite"):
    s = _serial(backend)
    seq_type = "SERIAL" if backend == PG else "INTEGER"
    seq_null = "NOT NULL" if backend == PG else ""
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            resource TEXT NOT NULL,
            detail TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            hash TEXT NOT NULL,
            id_seq {seq_type} {seq_null}
        )
    """,
    )
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS metrics_history (
            id {s},
            name TEXT NOT NULL,
            value REAL NOT NULL,
            timestamp TEXT NOT NULL,
            tags TEXT DEFAULT '{{}}'
        )
    """,
    )
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS health_history (
            id {s},
            service TEXT NOT NULL,
            healthy INTEGER NOT NULL,
            message TEXT DEFAULT '',
            latency_ms REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL
        )
    """,
    )
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS run_manifest (
            id {s},
            run_id TEXT NOT NULL,
            service TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL,
            metadata TEXT DEFAULT '{{}}'
        )
    """,
    )
    execute(conn, "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    execute(conn, "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
    execute(conn, "CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics_history(name)")
    execute(conn, "CREATE INDEX IF NOT EXISTS idx_health_service ON health_history(service)")
    execute(conn, "CREATE INDEX IF NOT EXISTS idx_manifest_service ON run_manifest(service)")


def down(conn, backend: str = "sqlite"):
    execute(conn, "DROP TABLE IF EXISTS run_manifest")
    execute(conn, "DROP TABLE IF EXISTS health_history")
    execute(conn, "DROP TABLE IF EXISTS metrics_history")
    execute(conn, "DROP TABLE IF EXISTS audit_log")
