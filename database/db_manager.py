import sqlite3
import os
from typing import List, Tuple, Optional
import pandas as pd
from configs.config_loader import config

class DatabaseManager:
    def __init__(self):
        self.db_path = config.get("database.path", "database/market_intelligence.db")
        self._ensure_db_dir()
        self.conn = sqlite3.connect(self.db_path)
        self._create_tables()

    def _ensure_db_dir(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _create_tables(self):
        cursor = self.conn.cursor()
        
        # Market Data Table
        cursor.execute('''
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
        ''')

        # Features Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                feature_name TEXT NOT NULL,
                feature_value REAL,
                UNIQUE(symbol, timestamp, feature_name)
            )
        ''')

        # Correlations Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS correlations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_a TEXT NOT NULL,
                symbol_b TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                correlation_value REAL,
                window_size INTEGER,
                UNIQUE(symbol_a, symbol_b, timestamp, window_size)
            )
        ''')

        # Journal Entries Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                content TEXT NOT NULL,
                tags TEXT
            )
        ''')

        # Liquidity Zones Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS liquidity_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                timeframe TEXT,
                zone_type TEXT,
                price_level REAL,
                strength REAL,
                detected_at DATETIME
            )
        ''')

        # Market Structure Events Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_structure_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                event_type TEXT,
                description TEXT,
                timestamp DATETIME
            )
        ''')

        # Session Statistics Table
        cursor.execute('''
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
        ''')

        # Knowledge Graph Tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                content TEXT NOT NULL,
                tags TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (observation_id) REFERENCES observations(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                test_params TEXT,
                results_summary TEXT,
                FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                metrics_json TEXT,
                visual_path TEXT,
                FOREIGN KEY (test_id) REFERENCES research_tests(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                content TEXT NOT NULL,
                tags TEXT,
                FOREIGN KEY (result_id) REFERENCES research_results(id)
            )
        ''')

        # Strategy Registry Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT,
                params_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Backtest Results Table
        cursor.execute('''
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
        ''')

        # Risk Limits Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_drawdown REAL,
                max_position_size REAL,
                max_correlation REAL,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        self.conn.commit()

    def save_ohlc(self, df: pd.DataFrame, symbol: str):
        """Save OHLC data from a DataFrame with upsert logic."""
        df = df.copy()
        df['symbol'] = symbol
        
        # Use a temporary table for upsert-like behavior in SQLite
        df.to_sql('temp_market_data', self.conn, if_exists='replace', index=False)
        self.conn.execute('''
            INSERT OR REPLACE INTO market_data (symbol, timestamp, open, high, low, close, volume)
            SELECT symbol, timestamp, open, high, low, close, volume FROM temp_market_data
        ''')
        self.conn.execute('DROP TABLE temp_market_data')
        self.conn.commit()

    def save_features(self, df: pd.DataFrame, symbol: str):
        """Persist computed features."""
        # Assume df has columns: timestamp, feature_name, feature_value
        df = df.copy()
        df['symbol'] = symbol
        df.to_sql('temp_features', self.conn, if_exists='replace', index=False)
        self.conn.execute('''
            INSERT OR REPLACE INTO features (symbol, timestamp, feature_name, feature_value)
            SELECT symbol, timestamp, feature_name, feature_value FROM temp_features
        ''')
        self.conn.execute('DROP TABLE temp_features')
        self.conn.commit()

    def save_correlations(self, df: pd.DataFrame):
        """Persist correlation matrix data."""
        # df should be a long-format DataFrame with symbol_a, symbol_b, timestamp, correlation_value, window_size
        df.to_sql('temp_correlations', self.conn, if_exists='replace', index=False)
        self.conn.execute('''
            INSERT OR REPLACE INTO correlations (symbol_a, symbol_b, timestamp, correlation_value, window_size)
            SELECT symbol_a, symbol_b, timestamp, correlation_value, window_size FROM temp_correlations
        ''')
        self.conn.execute('DROP TABLE temp_correlations')
        self.conn.commit()

    def save_liquidity_zones(self, zones_df: pd.DataFrame):
        """Persist liquidity zones."""
        zones_df.to_sql('liquidity_zones', self.conn, if_exists='append', index=False)
        self.conn.commit()

    def save_market_events(self, events_df: pd.DataFrame):
        """Persist market structure events."""
        events_df.to_sql('market_structure_events', self.conn, if_exists='append', index=False)
        self.conn.commit()

    def save_session_stats(self, stats_df: pd.DataFrame):
        """Persist session statistics."""
        # Ensure column names match schema
        stats_df = stats_df.rename(columns={'session': 'session_name'})
        if 'breakout_occurred' not in stats_df.columns:
            stats_df['breakout_occurred'] = False
            
        stats_df.to_sql('temp_session_stats', self.conn, if_exists='replace', index=False)
        self.conn.execute('''
            INSERT OR REPLACE INTO session_statistics (symbol, session_name, date, volatility, range_size, breakout_occurred)
            SELECT symbol, session_name, date, volatility, range_size, breakout_occurred FROM temp_session_stats
        ''')
        self.conn.execute('DROP TABLE temp_session_stats')
        self.conn.commit()

    def get_ohlc(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        query = f"SELECT * FROM market_data WHERE symbol = ? ORDER BY timestamp ASC LIMIT ?"
        return pd.read_sql_query(query, self.conn, params=(symbol, limit), parse_dates=['timestamp'])

    def close(self):
        self.conn.close()
