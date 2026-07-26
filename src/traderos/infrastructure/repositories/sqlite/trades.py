from __future__ import annotations

import sqlite3
import uuid

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.repositories.trade_repository import PositionRepository
from traderos.domain.repositories.trade_repository import TradeRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteTradeRepository(SQLiteRepository[Trade], TradeRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "trades"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Trade) -> dict:
        return {
            "id": str(entity.id),
            "signal_id": str(entity.signal_id),
            "market_id": str(entity.market_id),
            "side": entity.side.value,
            "quantity": entity.quantity,
            "price": entity.price,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Trade:
        return Trade(
            id=to_uuid(row["id"]),
            signal_id=to_uuid(row["signal_id"]),
            market_id=to_uuid(row["market_id"]),
            side=TradeSide(row["side"]),
            quantity=row["quantity"],
            price=row["price"],
            status=TradeStatus(row["status"]),
            created_at=to_dt(row["created_at"]),
        )

    def get_by_signal(self, signal_id: uuid.UUID) -> list[Trade]:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE signal_id = ? ORDER BY created_at",
            (str(signal_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_by_market(self, market_id: uuid.UUID) -> list[Trade]:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE market_id = ? ORDER BY created_at",
            (str(market_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLitePositionRepository(SQLiteRepository[Position], PositionRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "positions"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                pnl REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Position) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "quantity": entity.quantity,
            "entry_price": entity.entry_price,
            "current_price": entity.current_price,
            "pnl": entity.pnl,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Position:
        return Position(
            id=to_uuid(row["id"]),
            market_id=to_uuid(row["market_id"]),
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            current_price=row["current_price"],
            pnl=row["pnl"],
            created_at=to_dt(row["created_at"]),
        )

    def get_by_market(self, market_id: uuid.UUID) -> Position | None:
        cursor = self.conn.execute("SELECT * FROM positions WHERE market_id = ?", (str(market_id),))
        row = cursor.fetchone()
        return self._from_row(row) if row else None

    def list_open(self) -> list[Position]:
        cursor = self.conn.execute("SELECT * FROM positions WHERE quantity != 0")
        return [self._from_row(row) for row in cursor.fetchall()]
