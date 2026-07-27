from __future__ import annotations

import sqlite3

VERSION = 3
DESCRIPTION = "Strategy registry table with 3 built-in seed strategies"


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS strategy_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            params TEXT NOT NULL DEFAULT '{}',
            version TEXT NOT NULL DEFAULT '1.0.0',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        INSERT OR IGNORE INTO strategy_registry (id, name, params, version, status)
        VALUES
            ('moving_average_trend', 'moving_average_trend', '{}', '1.0.0', 'active'),
            ('volatility_breakout', 'volatility_breakout', '{}', '1.0.0', 'active'),
            ('mean_reversion', 'mean_reversion', '{}', '1.0.0', 'active');
        """)


def down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS strategy_registry")
