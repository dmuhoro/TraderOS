VERSION = 1
DESCRIPTION = (
    "Initial schema: market data, features, correlations,"
    " journal, liquidity, knowledge graph, strategy registry"
)

PG = "postgres"


def _serial(backend: str) -> str:
    return "SERIAL" if backend == PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _ref(backend: str) -> str:
    return "INTEGER"


def _dt(backend: str) -> str:
    return "TIMESTAMP" if backend == PG else "DATETIME"


def _bool(backend: str) -> str:
    return "BOOLEAN"


def up(conn, backend: str = "sqlite"):
    cursor = conn.cursor()
    s = _serial(backend)
    ref = _ref(backend)
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

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS liquidity_zones (
            id {s},
            symbol TEXT,
            timeframe TEXT,
            zone_type TEXT,
            price_level REAL,
            strength REAL,
            detected_at {dt}
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

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS observations (
            id {s},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            content TEXT NOT NULL,
            tags TEXT
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id {s},
            observation_id {ref},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (observation_id) REFERENCES observations(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS research_tests (
            id {s},
            hypothesis_id {ref},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            test_params TEXT,
            results_summary TEXT,
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS research_results (
            id {s},
            test_id {ref},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            metrics_json TEXT,
            visual_path TEXT,
            FOREIGN KEY (test_id) REFERENCES research_tests(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS lessons (
            id {s},
            result_id {ref},
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            content TEXT NOT NULL,
            tags TEXT,
            FOREIGN KEY (result_id) REFERENCES research_results(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS strategies (
            id {s},
            name TEXT UNIQUE,
            description TEXT,
            params_json TEXT,
            created_at {dt} DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id {s},
            strategy_id {ref},
            symbol TEXT,
            start_date TEXT,
            end_date TEXT,
            metrics_json TEXT,
            equity_curve_json TEXT,
            timestamp {dt} DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS risk_limits (
            id {s},
            max_drawdown REAL,
            max_position_size REAL,
            max_correlation REAL,
            is_active {bl} DEFAULT 1
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
        "strategies",
        "backtest_results",
        "risk_limits",
    ]
    for table in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
