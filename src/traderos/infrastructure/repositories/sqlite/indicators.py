from __future__ import annotations

import sqlite3
import uuid

from traderos.domain.entities import Indicator
from traderos.domain.entities import LiquidityZone
from traderos.domain.entities import ZoneType
from traderos.domain.repositories.indicator_repository import IndicatorRepository
from traderos.domain.repositories.liquidity_repository import LiquidityZoneRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteIndicatorRepository(SQLiteRepository[Indicator], IndicatorRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "indicators"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL
            )
            """)

    def _to_row(self, entity: Indicator) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "timestamp": entity.timestamp.isoformat(),
            "name": entity.name,
            "value": entity.value,
        }

    def _from_row(self, row: sqlite3.Row) -> Indicator:
        return Indicator(
            id=to_uuid(row["id"]),
            market_id=to_uuid(row["market_id"]),
            timestamp=to_dt(row["timestamp"]),
            name=row["name"],
            value=row["value"],
        )

    def get_by_name(self, market_id: uuid.UUID, name: str) -> list[Indicator]:
        cursor = self.conn.execute(
            "SELECT * FROM indicators WHERE market_id = ? AND name = ? ORDER BY timestamp",
            (str(market_id), name),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_latest(self, market_id: uuid.UUID, name: str) -> Indicator | None:
        sql = (
            "SELECT * FROM indicators WHERE market_id = ? AND name = ?"
            " ORDER BY timestamp DESC LIMIT 1"
        )
        cursor = self.conn.execute(sql, (str(market_id), name))
        row = cursor.fetchone()
        return self._from_row(row) if row else None


class SQLiteLiquidityZoneRepository(SQLiteRepository[LiquidityZone], LiquidityZoneRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "liquidity_zones"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_zones (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                price_level REAL NOT NULL,
                zone_type TEXT NOT NULL,
                strength INTEGER NOT NULL,
                detected_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: LiquidityZone) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "price_level": entity.price_level,
            "zone_type": entity.zone_type.value,
            "strength": entity.strength,
            "detected_at": entity.detected_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> LiquidityZone:
        return LiquidityZone(
            id=to_uuid(row["id"]),
            market_id=to_uuid(row["market_id"]),
            price_level=row["price_level"],
            zone_type=ZoneType(row["zone_type"]),
            strength=row["strength"],
            detected_at=to_dt(row["detected_at"]),
        )
