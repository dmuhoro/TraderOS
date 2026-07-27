VERSION = 2
DESCRIPTION = "Observability persistence: audit_log, metrics_history, health_history, run_manifest"


def up(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            resource TEXT NOT NULL,
            detail TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value REAL NOT NULL,
            timestamp TEXT NOT NULL,
            tags TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            healthy INTEGER NOT NULL,
            message TEXT DEFAULT '',
            latency_ms REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            service TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics_history(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_service ON health_history(service)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_manifest_service ON run_manifest(service)")


def down(conn):
    conn.execute("DROP TABLE IF EXISTS run_manifest")
    conn.execute("DROP TABLE IF EXISTS health_history")
    conn.execute("DROP TABLE IF EXISTS metrics_history")
    conn.execute("DROP TABLE IF EXISTS audit_log")
