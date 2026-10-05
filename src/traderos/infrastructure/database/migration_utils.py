"""Shared backend-aware SQL execution for migrations.

sqlite connections expose ``execute()`` directly; psycopg2/psycopg connections
require ``cursor().execute()``. Routing every migration statement through
``execute()`` keeps the PostgreSQL migration path working (H7/OT-005).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from traderos.infrastructure.database.safety_guard import guard_destructive_sql

PG = "postgres"


def detect_backend(conn: Any) -> str:
    if isinstance(conn, sqlite3.Connection):
        return "sqlite"
    if getattr(conn, "_backend", None) == "sqlite":
        return "sqlite"
    module = type(conn).__module__
    if module.startswith(("psycopg2", "psycopg")):
        return PG
    return PG


def execute(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    # Every migration and repository statement funnels through here, so this is
    # the one place that can refuse destructive SQL aimed at production.
    guard_destructive_sql(conn, sql)
    backend = detect_backend(conn)
    if backend == PG:
        cur = conn.cursor()
        cur.execute(sql, params or None)
        return cur
    if params:
        return conn.execute(sql, params)
    return conn.execute(sql)
