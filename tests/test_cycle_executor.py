from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


class _MockBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None):
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []


class _BadStrat(StrategyBase):
    name = "test_bad_strat"

    def evaluate(self, state):
        msg = "intentional error"
        raise RuntimeError(msg)


class _GoodStrat(StrategyBase):
    name = "test_good_strat"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "test"})


def _make_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


def _register(name, cls):
    strategy_registry._strategies[name] = cls


def _unregister(name):
    strategy_registry._strategies.pop(name, None)


class TestCycleExecutor:
    def test_backtest_mode_returns_early(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.BACKTEST,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert result.signals == 0
        assert result.trades == 0
        assert len(result.errors) == 0
        conn.close()

    def test_no_data_ingestion_runs_cleanly(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        mid = uuid.uuid4()
        result = executor.run(mid, 100.0, candle_time=datetime.now(UTC))
        assert result.market_id == mid
        assert result.timestamp is not None
        conn.close()

    def test_strategy_raise_caught_by_inner_except(self) -> None:
        conn = _make_conn()
        _register("test_bad_strat", _BadStrat)
        try:
            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=Mock(),
                risk_service=Mock(),
                portfolio_service=Mock(),
                execution=Mock(),
                analysis=Mock(),
                broker=_MockBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert len(result.errors) > 0
            assert any("test_bad_strat" in e for e in result.errors)
        finally:
            _unregister("test_bad_strat")
        conn.close()

    def test_data_ingestion_raise_caught_by_outer_except(self) -> None:
        conn = _make_conn()
        data_ingestion = Mock()
        data_ingestion.fetch_candles.side_effect = RuntimeError("outer failure")
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert len(result.errors) > 0
        assert "outer failure" in result.errors[0]
        conn.close()

    def test_cash_balance_live_mode(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.LIVE,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        assert executor._cash_balance() == 10000.0
        conn.close()

    def test_cash_balance_paper_mode(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            default_cash=50000.0,
        )
        assert executor._cash_balance() == 50000.0
        conn.close()

    def test_candles_processed_with_data_ingestion(self) -> None:
        from decimal import Decimal

        conn = _make_conn()
        data_ingestion = Mock()
        from traderos.domain.entities.candle import Candle
        from traderos.domain.entities.value_objects import OHLCV
        from traderos.domain.entities.value_objects import Timeframe

        data_ingestion.fetch_candles.return_value = [
            Candle(
                market_id=uuid.uuid4(),
                ohlcv=OHLCV(Decimal("100.0"), Decimal("101.0"), Decimal("99.0"), Decimal("100.5"), Decimal("1000.0")),
                timestamp=datetime.now(UTC),
                timeframe=Timeframe.MINUTE_1,
            )
        ] * 25
        from traderos.domain.services.analysis_service import AnalysisService

        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=AnalysisService(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert result.errors == []
        conn.close()
