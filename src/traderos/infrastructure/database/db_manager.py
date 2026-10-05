import os
import sqlite3
import uuid

import pandas as pd

from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database.migration_manager import migrate


def _market_id_for_symbol(symbol: object) -> str:
    """Deterministic market id for a symbol.

    The same ``uuid5("traders/{symbol}")`` scheme the factory and the daemon use
    to route ticks, so a zone written from a symbol frame refers to the same
    market the trading loop trades.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{symbol}"))


class DatabaseManager:
    def __init__(self):
        self.db_path = os.environ.get("DB_PATH") or Config.load().db_path
        self._ensure_db_dir()
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._run_migrations()

    def _ensure_db_dir(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    def _run_migrations(self):
        migrate(self.conn)

    def save_ohlc(self, df: pd.DataFrame, symbol: str):
        """Save OHLC data from a DataFrame with upsert logic."""
        df = df.copy()
        df["symbol"] = symbol

        # Use a temporary table for upsert-like behavior in SQLite
        df.to_sql("temp_market_data", self.conn, if_exists="replace", index=False)
        self.conn.execute("""
            INSERT OR REPLACE INTO market_data (symbol, timestamp, open, high, low, close, volume)
            SELECT symbol, timestamp, open, high, low, close, volume FROM temp_market_data
        """)
        self.conn.execute("DROP TABLE temp_market_data")
        self.conn.commit()

    def save_features(self, df: pd.DataFrame, symbol: str):
        """Persist computed features."""
        # Assume df has columns: timestamp, feature_name, feature_value
        df = df.copy()
        df["symbol"] = symbol
        df.to_sql("temp_features", self.conn, if_exists="replace", index=False)
        self.conn.execute("""
            INSERT OR REPLACE INTO features (symbol, timestamp, feature_name, feature_value)
            SELECT symbol, timestamp, feature_name, feature_value FROM temp_features
        """)
        self.conn.execute("DROP TABLE temp_features")
        self.conn.commit()

    def save_correlations(self, df: pd.DataFrame):
        """Persist correlation matrix data."""
        df.to_sql("temp_correlations", self.conn, if_exists="replace", index=False)
        self.conn.execute("""
            INSERT OR REPLACE INTO correlations
                (symbol_a, symbol_b, timestamp, correlation_value, window_size)
            SELECT symbol_a, symbol_b, timestamp, correlation_value, window_size
            FROM temp_correlations
        """)
        self.conn.execute("DROP TABLE temp_correlations")
        self.conn.commit()

    def save_liquidity_zones(self, zones_df: pd.DataFrame):
        """Persist liquidity zones.

        The dataframe is renamed to the schema before writing. ``liquidity_zones``
        is declared by the LiquidityZone repository (market_id/price_level/
        zone_type/strength/detected_at, see repositories/sqlite/indicators.py) and
        by v001 to match it, but callers legitimately arrive with the older
        analysis-shaped frame that carries ``symbol``/``timeframe`` instead of
        ``market_id``.

        Writing the frame as-is raised
        ``sqlite3.OperationalError: table liquidity_zones has no column named
        symbol`` -- the caller and the table disagreed and the failure was
        reported from deep inside pandas, naming neither side as the cause.
        Translating the two legacy columns here keeps the drop-in contract and
        puts the mismatch at the boundary that owns it.
        """
        zones_df = zones_df.copy()
        if "symbol" in zones_df.columns and "market_id" not in zones_df.columns:
            zones_df["market_id"] = zones_df["symbol"].map(_market_id_for_symbol)
        for legacy in ("symbol", "timeframe"):
            if legacy in zones_df.columns:
                zones_df = zones_df.drop(columns=[legacy])
        # liquidity_zones.id is TEXT PRIMARY KEY NOT NULL. The incoming frame
        # carries no id, so to_sql omitted the column entirely and SQLite stored
        # NULL -- legal for a TEXT PRIMARY KEY, which unlike PostgreSQL does not
        # imply NOT NULL. Every zone was therefore written without an identity
        # and could not be addressed by key. Generating the id here restores the
        # row's identity; an explicit caller-supplied id is preserved.
        if "id" not in zones_df.columns:
            zones_df.insert(0, "id", [str(uuid.uuid4()) for _ in range(len(zones_df))])
        zones_df.to_sql("liquidity_zones", self.conn, if_exists="append", index=False)
        self.conn.commit()

    def save_market_events(self, events_df: pd.DataFrame):
        """Persist market structure events."""
        events_df.to_sql("market_structure_events", self.conn, if_exists="append", index=False)
        self.conn.commit()

    def save_session_stats(self, stats_df: pd.DataFrame):
        """Persist session statistics."""
        # Ensure column names match schema
        stats_df = stats_df.rename(columns={"session": "session_name"})
        if "breakout_occurred" not in stats_df.columns:
            stats_df["breakout_occurred"] = False

        stats_df.to_sql("temp_session_stats", self.conn, if_exists="replace", index=False)
        self.conn.execute("""
            INSERT OR REPLACE INTO session_statistics
                (symbol, session_name, date, volatility, range_size, breakout_occurred)
            SELECT symbol, session_name, date, volatility, range_size, breakout_occurred
            FROM temp_session_stats
        """)
        self.conn.execute("DROP TABLE temp_session_stats")
        self.conn.commit()

    def get_ohlc(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        query = "SELECT * FROM market_data WHERE symbol = ? ORDER BY timestamp ASC LIMIT ?"
        return pd.read_sql_query(
            query, self.conn, params=[symbol, limit], parse_dates=["timestamp"]
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.conn:
            self.conn.close()
