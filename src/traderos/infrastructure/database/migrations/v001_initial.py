VERSION = 1
DESCRIPTION = (
    "Initial schema: market data, features, correlations,"
    " journal, liquidity, knowledge graph, strategy registry"
)

PG = "postgres"


# Tables created by this migration. The version marker is a claim, not a proof:
# migration_manager.schema_drift() checks these actually exist so a marker at head
# can never silently paper over a missing table.
TABLES: tuple[str, ...] = (
    "market_data",
    "features",
    "correlations",
    "journal_entries",
    "liquidity_zones",
    "market_structure_events",
    "session_statistics",
    "observations",
    "hypotheses",
    "research_tests",
    "research_results",
    "lessons",
    "risk_limits",
)


def _serial(backend: str) -> str:
    return "SERIAL PRIMARY KEY" if backend == PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


# The research/indicator tables below are ALSO created at runtime by their
# repositories (see repositories/{sqlite,postgres}/research.py and
# sqlite/indicators.py). Both owners must agree, because `CREATE TABLE IF NOT
# EXISTS` means whichever runs FIRST wins and the loser is silently ignored.
#
# These declarations previously used SERIAL/INTEGER surrogate keys while the
# repositories used TEXT holding uuid4 values -- the domain entities
# (Observation.id, Hypothesis.observation_id, Lesson.result_id) are all
# uuid.UUID, and the repositories write `str(entity.id)`. On a fresh PostgreSQL
# database the mismatch is not cosmetic: if a repository created `observations`
# first, v001's `hypotheses` FK (`observation_id INTEGER REFERENCES
# observations(id)`) can never be implemented against a TEXT primary key, and
# PostgreSQL raises DatatypeMismatch. That aborted v001 mid-run, so every
# migration after it was skipped and ~25 API tests failed with a confusing
# error.
#
# The repository DDL is authoritative -- it matches the domain types the code
# actually writes -- so these tables are declared to match it exactly. That
# makes the two owners agree regardless of which runs first.
def _text_pk() -> str:
    # NOT NULL is explicit, not implied. In PostgreSQL a PRIMARY KEY already
    # implies it; in SQLite ``TEXT PRIMARY KEY`` does not, so a row with a NULL id
    # is silently accepted. That is how research tables ended up holding rows
    # with no identity: the writer read cursor.lastrowid, got None, and the row
    # was unreachable by any join. Declaring NOT NULL makes SQLite refuse the row
    # the same way PostgreSQL does.
    return "TEXT PRIMARY KEY NOT NULL"


def _dt(backend: str) -> str:
    return "TIMESTAMP" if backend == PG else "DATETIME"


def _bool(backend: str) -> str:
    return "BOOLEAN"


def up(conn, backend: str = "sqlite"):
    cursor = conn.cursor()
    s = _serial(backend)
    dt = _dt(backend)
    bl = _bool(backend)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS market_data (
            id {s},
            symbol TEXT NOT NULL,
            timestamp {dt} NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            UNIQUE(symbol, timestamp)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS features (
            id {s},
            symbol TEXT NOT NULL,
            timestamp {dt} NOT NULL,
            feature_name TEXT NOT NULL,
            feature_value REAL,
            UNIQUE(symbol, timestamp, feature_name)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS correlations (
            id {s},
            symbol_a TEXT NOT NULL,
            symbol_b TEXT NOT NULL,
            timestamp {dt} NOT NULL,
            correlation_value REAL,
            window_size INTEGER,
            UNIQUE(symbol_a, symbol_b, timestamp, window_size)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id {s},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            content TEXT NOT NULL,
            tags TEXT
        )
    """)

    # Matches sqlite/indicators.py's LiquidityZoneRepository DDL, which is the
    # only owner of this table (there is no PostgreSQL variant). Same
    # first-wins hazard as the research tables above.
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS liquidity_zones (
            id {_text_pk()},
            market_id TEXT NOT NULL,
            price_level REAL NOT NULL,
            zone_type TEXT NOT NULL,
            strength INTEGER NOT NULL,
            detected_at TEXT NOT NULL
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS market_structure_events (
            id {s},
            symbol TEXT,
            event_type TEXT,
            description TEXT,
            timestamp {dt}
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS session_statistics (
            id {s},
            symbol TEXT,
            session_name TEXT,
            date TEXT,
            volatility REAL,
            range_size REAL,
            breakout_occurred {bl},
            UNIQUE(symbol, session_name, date)
        )
    """)

    # Column shapes below mirror the repository DDL exactly (see the comment on
    # _text_pk): TEXT uuid keys, TEXT timestamps in ISO-8601, TEXT tags holding a
    # JSON array. Diverging here reintroduces the DatatypeMismatch above.
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS observations (
            id {_text_pk()},
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]'
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id {_text_pk()},
            observation_id TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL
        )
    """)

    # Legacy research pair. v009 adds the canonical experiments/experiment_results
    # the current repo contract uses; kept so the version chain stays contiguous
    # and v009's comment about the legacy tables remains true.
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS research_tests (
            id {_text_pk()},
            hypothesis_id TEXT NOT NULL,
            test_params TEXT,
            results_summary TEXT
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS research_results (
            id {_text_pk()},
            test_id TEXT NOT NULL,
            metrics_json TEXT,
            visual_path TEXT
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS lessons (
            id {_text_pk()},
            result_id TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS risk_limits (
            id {s},
            max_drawdown REAL,
            max_position_size REAL,
            max_correlation REAL,
            is_active {bl} DEFAULT TRUE
        )
    """)

    conn.commit()


def down(conn, backend: str = "sqlite"):
    cursor = conn.cursor()
    tables = [
        "market_data",
        "features",
        "correlations",
        "journal_entries",
        "liquidity_zones",
        "market_structure_events",
        "session_statistics",
        "observations",
        "hypotheses",
        "research_tests",
        "research_results",
        "lessons",
        "risk_limits",
    ]
    for table in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
