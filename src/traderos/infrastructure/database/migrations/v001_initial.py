VERSION = 1
DESCRIPTION = (
    "Initial schema: market data, features, correlations,"
    " journal, liquidity, knowledge graph, strategy registry"
)


def up(conn):
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            UNIQUE(symbol, timestamp)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            feature_name TEXT NOT NULL,
            feature_value REAL,
            UNIQUE(symbol, timestamp, feature_name)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS correlations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol_a TEXT NOT NULL,
            symbol_b TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            correlation_value REAL,
            window_size INTEGER,
            UNIQUE(symbol_a, symbol_b, timestamp, window_size)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            content TEXT NOT NULL,
            tags TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timeframe TEXT,
            zone_type TEXT,
            price_level REAL,
            strength REAL,
            detected_at DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_structure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            event_type TEXT,
            description TEXT,
            timestamp DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            session_name TEXT,
            date TEXT,
            volatility REAL,
            range_size REAL,
            breakout_occurred BOOLEAN,
            UNIQUE(symbol, session_name, date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            content TEXT NOT NULL,
            tags TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (observation_id) REFERENCES observations(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            test_params TEXT,
            results_summary TEXT,
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            metrics_json TEXT,
            visual_path TEXT,
            FOREIGN KEY (test_id) REFERENCES research_tests(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            content TEXT NOT NULL,
            tags TEXT,
            FOREIGN KEY (result_id) REFERENCES research_results(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            params_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER,
            symbol TEXT,
            start_date TEXT,
            end_date TEXT,
            metrics_json TEXT,
            equity_curve_json TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            max_drawdown REAL,
            max_position_size REAL,
            max_correlation REAL,
            is_active BOOLEAN DEFAULT 1
        )
    """)

    conn.commit()


def down(conn):
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
