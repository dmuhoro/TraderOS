import os
import sqlite3

import pandas as pd

from traderos.infrastructure.config.config_loader import config
from traderos.infrastructure.database.migration_manager import migrate


class DatabaseManager:
    def __init__(self):
        self.db_path = os.environ.get("DB_PATH") or config.db_path
        self._ensure_db_dir()
        self.conn = sqlite3.connect(self.db_path)
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
        """Persist liquidity zones."""
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

    def close(self):
        self.conn.close()
