from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.repositories.signal_repository import SignalRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteSignalRepository(SQLiteRepository[Signal], SignalRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "signals"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence REAL NOT NULL,
                generated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Signal) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "strategy_id": str(entity.strategy_id),
            "direction": entity.direction.value,
            "confidence": entity.confidence,
            "generated_at": entity.generated_at.isoformat(),
            "expires_at": entity.expires_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Signal:
        return Signal(
            id=to_uuid(row["id"]),
            market_id=to_uuid(row["market_id"]),
            strategy_id=to_uuid(row["strategy_id"]),
            direction=SignalDirection(row["direction"]),
            confidence=row["confidence"],
            generated_at=to_dt(row["generated_at"]),
            expires_at=to_dt(row["expires_at"]),
        )

    def get_active(self, market_id: uuid.UUID) -> list[Signal]:
        now = datetime.now(UTC)
        sql = (
            "SELECT * FROM signals WHERE market_id = ? AND expires_at > ?"
            " ORDER BY generated_at DESC"
        )
        cursor = self.conn.execute(sql, (str(market_id), now.isoformat()))
        return [s for s in (self._from_row(row) for row in cursor.fetchall()) if s.expires_at > now]

    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[Signal]:
        cursor = self.conn.execute(
            "SELECT * FROM signals WHERE strategy_id = ? ORDER BY generated_at DESC",
            (str(strategy_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_range(
        self,
        market_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> list[Signal]:
        sql = (
            "SELECT * FROM signals WHERE market_id = ?"
            " AND generated_at >= ? AND generated_at <= ?"
            " ORDER BY generated_at"
        )
        cursor = self.conn.execute(sql, (str(market_id), start.isoformat(), end.isoformat()))
        return [self._from_row(row) for row in cursor.fetchall()]
