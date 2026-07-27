from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import cast

from traderos.domain.entities import BacktestResult
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Metrics
from traderos.domain.entities import Strategy
from traderos.domain.entities import StrategyStatus
from traderos.domain.repositories.strategy_repository import BacktestResultRepository
from traderos.domain.repositories.strategy_repository import StrategyRepository
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import from_json
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_json
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteStrategyRepository(SQLiteRepository[Strategy], StrategyRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "strategies"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                params TEXT NOT NULL DEFAULT '{}',
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Strategy) -> dict:
        return {
            "id": str(entity.id),
            "name": entity.name,
            "params": to_json(entity.params),
            "version": entity.version,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Strategy:
        params = cast(dict, from_json(row["params"]) or {})
        return Strategy(
            id=to_uuid(row["id"]),
            name=row["name"],
            params=params,
            version=row["version"],
            status=StrategyStatus(row["status"]),
            created_at=to_dt(row["created_at"]),
        )

    def get_by_name(self, name: str) -> Strategy | None:
        cursor = self.conn.execute("SELECT * FROM strategies WHERE name = ?", (name,))
        row = cursor.fetchone()
        return self._from_row(row) if row else None

    def list_active(self) -> list[Strategy]:
        cursor = self.conn.execute("SELECT * FROM strategies WHERE status = ?", ("active",))
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteBacktestResultRepository(SQLiteRepository[BacktestResult], BacktestResultRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "backtest_results"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                total_return REAL NOT NULL DEFAULT 0.0,
                sharpe_ratio REAL NOT NULL DEFAULT 0.0,
                sortino_ratio REAL NOT NULL DEFAULT 0.0,
                calmar_ratio REAL NOT NULL DEFAULT 0.0,
                max_drawdown REAL NOT NULL DEFAULT 0.0,
                win_rate REAL NOT NULL DEFAULT 0.0,
                profit_factor REAL NOT NULL DEFAULT 0.0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                expectancy REAL NOT NULL DEFAULT 0.0,
                recovery_factor REAL NOT NULL DEFAULT 0.0,
                equity_curve TEXT NOT NULL DEFAULT '{"points": []}',
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: BacktestResult) -> dict:
        m = entity.metrics
        return {
            "id": str(entity.id),
            "strategy_id": str(entity.strategy_id),
            "market_id": str(entity.market_id),
            "total_return": m.total_return,
            "sharpe_ratio": m.sharpe_ratio,
            "sortino_ratio": m.sortino_ratio,
            "calmar_ratio": m.calmar_ratio,
            "max_drawdown": m.max_drawdown,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "total_trades": m.total_trades,
            "expectancy": m.expectancy,
            "recovery_factor": m.recovery_factor,
            "equity_curve": to_json(
                {"points": [(p[0].isoformat(), p[1]) for p in entity.equity_curve.points]}
            ),
            "period_start": entity.period_start.isoformat(),
            "period_end": entity.period_end.isoformat(),
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> BacktestResult:
        ec_data = cast(dict, from_json(row["equity_curve"]) or {"points": []})
        points: list[tuple[datetime, float]] = []
        for p in ec_data.get("points", []):
            points.append((to_dt(p[0]), float(p[1])))
        return BacktestResult(
            id=to_uuid(row["id"]),
            strategy_id=to_uuid(row["strategy_id"]),
            market_id=to_uuid(row["market_id"]),
            metrics=Metrics(
                total_return=row["total_return"],
                sharpe_ratio=row["sharpe_ratio"],
                sortino_ratio=row["sortino_ratio"],
                calmar_ratio=row["calmar_ratio"],
                max_drawdown=row["max_drawdown"],
                win_rate=row["win_rate"],
                profit_factor=row["profit_factor"],
                total_trades=row["total_trades"],
                expectancy=row["expectancy"],
                recovery_factor=row["recovery_factor"],
            ),
            equity_curve=EquityCurve(points=tuple(points)),
            period_start=to_dt(row["period_start"]),
            period_end=to_dt(row["period_end"]),
            created_at=to_dt(row["created_at"]),
        )

    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[BacktestResult]:
        cursor = self.conn.execute(
            "SELECT * FROM backtest_results WHERE strategy_id = ? ORDER BY created_at",
            (str(strategy_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_by_market(self, market_id: uuid.UUID) -> list[BacktestResult]:
        cursor = self.conn.execute(
            "SELECT * FROM backtest_results WHERE market_id = ? ORDER BY created_at",
            (str(market_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]
