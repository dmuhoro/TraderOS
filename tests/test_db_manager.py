from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from traderos.infrastructure.database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path) -> DatabaseManager:
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager.__new__(DatabaseManager)
    db.db_path = db_path
    db._ensure_db_dir()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    db.conn = conn
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return db


class TestDatabaseManager:
    def test_context_manager_enters_and_exits(self, db) -> None:
        with db as mgr:
            assert mgr is db
            assert db.conn is not None
        with pytest.raises(sqlite3.ProgrammingError):
            db.conn.execute("SELECT 1")

    def test_save_and_get_ohlc(self, db) -> None:
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01"]),
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [102.0],
                "volume": [1000.0],
            }
        )
        db.save_ohlc(df, "BTCUSDT")
        result = db.get_ohlc("BTCUSDT")
        assert len(result) == 1
        assert float(result.iloc[0]["close"]) == 102.0

    def test_save_features(self, db) -> None:
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01"]),
                "feature_name": ["sma_20"],
                "feature_value": [100.5],
            }
        )
        db.save_features(df, "BTCUSDT")
        cursor = db.conn.execute(
            "SELECT feature_value FROM features WHERE symbol = ?", ("BTCUSDT",)
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 100.5

    def test_save_correlations(self, db) -> None:
        df = pd.DataFrame(
            {
                "symbol_a": ["BTC"],
                "symbol_b": ["ETH"],
                "timestamp": [pd.to_datetime("2024-01-01")],
                "correlation_value": [0.85],
                "window_size": [30],
            }
        )
        db.save_correlations(df)
        cursor = db.conn.execute(
            "SELECT correlation_value FROM correlations WHERE symbol_a = ? AND symbol_b = ?",
            ("BTC", "ETH"),
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 0.85

    def test_save_liquidity_zones(self, db) -> None:
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "timeframe": ["1h"],
                "zone_type": ["Support"],
                "price_level": [50000.0],
                "strength": [3],
                "detected_at": [pd.to_datetime("2024-01-01")],
            }
        )
        db.save_liquidity_zones(df)
        cursor = db.conn.execute("SELECT zone_type FROM liquidity_zones")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Support"

    def test_save_market_events(self, db) -> None:
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "event_type": ["breakout"],
                "description": ["resistance broken"],
                "timestamp": [pd.to_datetime("2024-01-01")],
            }
        )
        db.save_market_events(df)
        cursor = db.conn.execute("SELECT event_type FROM market_structure_events")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "breakout"

    def test_save_session_stats(self, db) -> None:
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "session_name": ["London"],
                "date": ["2024-01-01"],
                "volatility": [0.02],
                "range_size": [500.0],
                "breakout_occurred": [True],
            }
        )
        db.save_session_stats(df)
        cursor = db.conn.execute("SELECT volatility FROM session_statistics")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 0.02

    def test_save_session_stats_renames_session_column(self, db) -> None:
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "session": ["New York"],
                "date": ["2024-01-01"],
                "volatility": [0.015],
                "range_size": [300.0],
            }
        )
        db.save_session_stats(df)
        cursor = db.conn.execute("SELECT session_name FROM session_statistics")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "New York"

    def test_get_ohlc_returns_empty_for_nonexistent_symbol(self, db) -> None:
        result = db.get_ohlc("NONEXISTENT")
        assert len(result) == 0

    def test_close(self, db) -> None:
        conn = db.conn
        db.close()
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
