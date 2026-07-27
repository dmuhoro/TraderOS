from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from traderos.domain.entities import OHLCV
from traderos.domain.entities import AssetClass
from traderos.domain.entities import Candle
from traderos.domain.entities import Market
from traderos.domain.entities import MarketStatus
from traderos.domain.entities import Timeframe
from traderos.domain.repositories.market_data_repository import CandleRepository
from traderos.domain.repositories.market_data_repository import MarketDataRepository
from traderos.domain.repositories.market_data_repository import MarketRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteMarketRepository(SQLiteRepository[Market], MarketRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "markets"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                exchange TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Market) -> dict:
        return {
            "id": str(entity.id),
            "symbol": entity.symbol,
            "asset_class": entity.asset_class.value,
            "exchange": entity.exchange,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Market:
        return Market(
            id=to_uuid(row["id"]),
            symbol=row["symbol"],
            asset_class=AssetClass(row["asset_class"]),
            exchange=row["exchange"],
            status=MarketStatus(row["status"]),
            created_at=to_dt(row["created_at"]),
        )

    def get_by_symbol(self, symbol: str) -> Market | None:
        cursor = self.conn.execute("SELECT * FROM markets WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        return self._from_row(row) if row else None

    def list_active(self) -> list[Market]:
        cursor = self.conn.execute("SELECT * FROM markets WHERE status = ?", ("active",))
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteCandleRepository(SQLiteRepository[Candle], CandleRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "candles"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT ''
            )
            """)

    def _to_row(self, entity: Candle) -> dict:
        return {
            "id": str(entity.id),
            "market_id": str(entity.market_id),
            "open": str(entity.ohlcv.open),
            "high": str(entity.ohlcv.high),
            "low": str(entity.ohlcv.low),
            "close": str(entity.ohlcv.close),
            "volume": str(entity.ohlcv.volume),
            "timestamp": entity.timestamp.isoformat(),
            "timeframe": entity.timeframe.value,
            "source": entity.source,
        }

    def _from_row(self, row: sqlite3.Row) -> Candle:
        from decimal import Decimal

        return Candle(
            id=to_uuid(row["id"]),
            market_id=to_uuid(row["market_id"]),
            ohlcv=OHLCV(
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            ),
            timestamp=to_dt(row["timestamp"]),
            timeframe=Timeframe(row["timeframe"]),
            source=row["source"],
        )

    def get_range(
        self,
        market_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        cursor = self.conn.execute(
            "SELECT * FROM candles WHERE market_id = ? AND timestamp >= ? AND timestamp <= ?",
            (str(market_id), start.isoformat(), end.isoformat()),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_latest(
        self,
        market_id: uuid.UUID,
        limit: int = 100,
    ) -> list[Candle]:
        cursor = self.conn.execute(
            "SELECT * FROM candles WHERE market_id = ? ORDER BY timestamp DESC LIMIT ?",
            (str(market_id), limit),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def delete_by_market(self, market_id: uuid.UUID) -> None:
        self.conn.execute("DELETE FROM candles WHERE market_id = ?", (str(market_id),))
        self.conn.commit()


class SQLiteMarketDataRepository(MarketDataRepository):
    def __init__(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._markets = SQLiteMarketRepository(connection)
        self._candles = SQLiteCandleRepository(connection)

    def get_market(self, symbol: str) -> Market | None:
        return self._markets.get_by_symbol(symbol)

    def get_candles(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Candle]:
        market = self._markets.get_by_symbol(symbol)
        if market is None:
            return []
        if start and end:
            return self._candles.get_range(market.id, start, end)
        return self._candles.get_latest(market.id, limit)

    def save_candle(self, candle: Candle) -> Candle:
        return self._candles.add(candle)
