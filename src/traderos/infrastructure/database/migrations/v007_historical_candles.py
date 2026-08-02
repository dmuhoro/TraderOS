from traderos.infrastructure.database.migration_utils import execute

VERSION = 7
DESCRIPTION = "Historical candle store: durable provider candles for backtesting/simulation"

PG = "postgres"


def _serial(backend: str) -> str:
    return "SERIAL PRIMARY KEY" if backend == PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def up(conn, backend: str = "sqlite"):
    s = _serial(backend)
    execute(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS historical_candles (
            id {s},
            source TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE (source, symbol, timeframe, ts)
        )
    """,
    )
    execute(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_historical_candles_lookup "
        "ON historical_candles (source, symbol, timeframe, ts)",
    )


def down(conn, backend: str = "sqlite"):
    execute(conn, "DROP TABLE IF EXISTS historical_candles")
