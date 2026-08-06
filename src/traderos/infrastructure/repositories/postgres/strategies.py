from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from typing import cast

from traderos.domain.entities import BacktestResult
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Metrics
from traderos.domain.entities import Strategy
from traderos.domain.entities import StrategyStatus
from traderos.domain.repositories.strategy_repository import BacktestResultRepository
from traderos.domain.repositories.strategy_repository import StrategyRepository
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import from_json
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_json
from traderos.infrastructure.repositories.postgres.base import to_uuid


class PostgresStrategyRepository(PostgresRepository[Strategy], StrategyRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "strategies"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE,
                    params TEXT NOT NULL DEFAULT '{}',
                    version TEXT NOT NULL DEFAULT '1.0.0',
                    status TEXT NOT NULL DEFAULT 'draft',
                    template TEXT,
                    created_at TEXT NOT NULL
                )
            """)
        self.conn.commit()

    def _to_row(self, entity: Strategy) -> dict:
        return {
            "id": str(entity.id),
            "name": entity.name,
            "params": to_json(entity.params),
            "version": entity.version,
            "status": entity.status.value,
            "template": entity.template,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Strategy:
        return Strategy(
            id=to_uuid(row[0]),
            name=row[1],
            params=cast(dict, from_json(row[2]) or {}),
            version=row[3],
            status=StrategyStatus(row[4]),
            template=row[5],
            created_at=to_dt(row[6]),
        )

    def get_by_name(self, name: str) -> Strategy | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM strategies WHERE name = %s", (name,))
            row = cur.fetchone()
        return self._from_row(row) if row else None

    def list_active(self) -> list[Strategy]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM strategies WHERE status IN (%s, %s)",
                (StrategyStatus.ACTIVE.value, StrategyStatus.PROMOTED.value),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresBacktestResultRepository(
    PostgresRepository[BacktestResult], BacktestResultRepository
):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "backtest_results"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
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
        self.conn.commit()

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

    def _from_row(self, row: Any) -> BacktestResult:
        ec_data = cast(dict, from_json(row[13]) or {"points": []})
        points: list[tuple[datetime, float]] = []
        for p in ec_data.get("points", []):
            points.append((to_dt(p[0]), float(p[1])))
        return BacktestResult(
            id=to_uuid(row[0]),
            strategy_id=to_uuid(row[1]),
            market_id=to_uuid(row[2]),
            metrics=Metrics(
                total_return=row[3],
                sharpe_ratio=row[4],
                sortino_ratio=row[5],
                calmar_ratio=row[6],
                max_drawdown=row[7],
                win_rate=row[8],
                profit_factor=row[9],
                total_trades=row[10],
                expectancy=row[11],
                recovery_factor=row[12],
            ),
            equity_curve=EquityCurve(points=tuple(points)),
            period_start=to_dt(row[14]),
            period_end=to_dt(row[15]),
            created_at=to_dt(row[16]),
        )

    def get_by_strategy(self, strategy_id: uuid.UUID) -> list[BacktestResult]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM backtest_results WHERE strategy_id = %s ORDER BY created_at",
                (str(strategy_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_market(self, market_id: uuid.UUID) -> list[BacktestResult]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM backtest_results WHERE market_id = %s ORDER BY created_at",
                (str(market_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]
