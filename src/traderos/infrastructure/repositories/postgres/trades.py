from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.repositories.trade_repository import PositionRepository
from traderos.domain.repositories.trade_repository import TradeRepository
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_uuid


class PostgresTradeRepository(PostgresRepository[Trade], TradeRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "trades"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    filled_quantity REAL DEFAULT 0.0,
                    filled_price REAL DEFAULT 0.0,
                    filled_at TEXT,
                    external_order_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
        self.conn.commit()

    def _to_row(self, entity: Trade) -> dict:
        return {
            "id": str(entity.id),
            "signal_id": str(entity.signal_id),
            "market_id": str(entity.market_id),
            "side": entity.side.value,
            "quantity": entity.quantity,
            "price": entity.price,
            "status": entity.status.value,
            "filled_quantity": entity.filled_quantity,
            "filled_price": entity.filled_price,
            "filled_at": entity.filled_at.isoformat() if entity.filled_at else None,
            "external_order_id": entity.external_order_id,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Trade:
        trade = Trade(
            id=to_uuid(row[0]),
            signal_id=to_uuid(row[1]),
            market_id=to_uuid(row[2]),
            side=TradeSide(row[3]),
            quantity=row[4],
            price=row[5],
            status=TradeStatus(row[6]),
            created_at=to_dt(row[11]),
        )
        trade.filled_quantity = row[7] or 0.0
        trade.filled_price = row[8] or 0.0
        if row[9]:
            trade.filled_at = to_dt(row[9])
        trade.external_order_id = row[10] if len(row) > 10 else None
        trade.updated_at = to_dt(row[12])
        return trade

    def get_by_signal(self, signal_id: uuid.UUID) -> list[Trade]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM trades WHERE signal_id = %s ORDER BY created_at",
                (str(signal_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_market(self, market_id: uuid.UUID) -> list[Trade]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM trades WHERE market_id = %s ORDER BY created_at",
                (str(market_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_open(self) -> list[Trade]:
        open_values = (
            TradeStatus.PENDING.value,
            TradeStatus.SUBMITTED.value,
            TradeStatus.PARTIALLY_FILLED.value,
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM trades WHERE status IN (%s, %s, %s) ORDER BY created_at",
                open_values,
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresPositionRepository(PostgresRepository[Position], PositionRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "positions"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    market_id TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    pnl REAL NOT NULL,
                    realized_pnl REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
        self.conn.commit()

    def _to_row(self, entity: Position) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "quantity": entity.quantity,
            "entry_price": entity.entry_price,
            "current_price": entity.current_price,
            "pnl": entity.pnl,
            "realized_pnl": entity.realized_pnl,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Position:
        pos = Position(
            id=to_uuid(row[0]),
            market_id=to_uuid(row[1]),
            quantity=row[2],
            entry_price=row[3],
            current_price=row[4],
            pnl=row[5],
            created_at=to_dt(row[7]),
        )
        pos.realized_pnl = row[6] or 0.0
        pos.updated_at = to_dt(row[8])
        return pos

    def get_by_market(self, market_id: uuid.UUID) -> Position | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE market_id = %s", (str(market_id),))
            row = cur.fetchone()
        return self._from_row(row) if row else None

    def list_open(self) -> list[Position]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE quantity != 0")
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]
