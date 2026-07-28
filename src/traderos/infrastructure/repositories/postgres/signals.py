from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.repositories.signal_repository import SignalRepository
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_uuid


class PostgresSignalRepository(PostgresRepository[Signal], SignalRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "signals"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
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
        self.conn.commit()

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

    def _from_row(self, row: Any) -> Signal:
        return Signal(
            id=to_uuid(row[0]),
            market_id=to_uuid(row[1]),
            strategy_id=to_uuid(row[2]),
            direction=SignalDirection(row[3]),
            confidence=row[4],
            generated_at=to_dt(row[5]),
            expires_at=to_dt(row[6]),
        )

    def get_active(self, market_id: uuid.UUID) -> list[Signal]:
        now = datetime.now(UTC)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM signals WHERE market_id = %s AND expires_at > %s"
                " ORDER BY generated_at DESC",
                (str(market_id), now.isoformat()),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[Signal]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM signals WHERE strategy_id = %s ORDER BY generated_at DESC",
                (str(strategy_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_range(
        self,
        market_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> list[Signal]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM signals WHERE market_id = %s"
                " AND generated_at >= %s AND generated_at <= %s"
                " ORDER BY generated_at",
                (str(market_id), start.isoformat(), end.isoformat()),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]
