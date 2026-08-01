from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

from traderos.application.orchestrator import CycleResult
from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.run_manifest import RunManifestService


class _BacktestStrat(StrategyBase):
    name = "test_orchestrator_backtest"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "test"})


class MockBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None):
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_stop_order(self, market_id, side, quantity, stop_price, market_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def place_trailing_stop_order(
        self, market_id, side, quantity, trail_percent, market_price=None
    ):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def modify_order(
        self, order_id, qty=None, limit_price=None, stop_price=None, trail_percent=None
    ):
        return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


class TestTradingOrchestrator:
    def _make(self, mode: TradingMode = TradingMode.PAPER) -> TradingOrchestrator:
        return TradingOrchestrator(
            mode=mode,
            signal_service=Mock(),
            risk_service=RiskService(),
            portfolio_service=Mock(spec=PortfolioService),
            execution=ExecutionService(),
            analysis=Mock(),
            broker=MockBroker(),
            backtest=BacktestingService(execution=ExecutionService()),
            paper=None,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
            market_ids=[uuid.uuid4()],
        )

    def test_start_and_stop(self) -> None:
        orch = self._make()
        orch.start()
        assert orch.running
        assert orch.health.get_status("orchestrator") is True
        orch.stop()
        assert not orch.running

    def test_run_cycle_backtest_mode(self) -> None:
        strategy_registry._strategies[_BacktestStrat.name] = _BacktestStrat
        try:
            orch = self._make(TradingMode.BACKTEST)
            orch.start()
            result = orch.run_cycle(uuid.uuid4(), 100.0)
            assert isinstance(result, CycleResult)
            assert result.signals > 0
            assert result.trades > 0
            orch.stop()
        finally:
            strategy_registry._strategies.pop(_BacktestStrat.name, None)

    def test_run_cycle_paper_mode(self) -> None:
        orch = self._make(TradingMode.PAPER)
        orch.start()
        mid = uuid.uuid4()
        result = orch.run_cycle(mid, 100.0, candle_time=datetime.now(UTC))
        assert isinstance(result, CycleResult)
        assert result.market_id == mid
        orch.stop()

    def test_get_status(self) -> None:
        orch = self._make()
        orch.start()
        status = orch.get_status()
        assert status["mode"] == "paper"
        assert status["running"] is True
        assert "health" in status
        assert "metrics" in status
        orch.stop()

    def test_multiple_cycles(self) -> None:
        orch = self._make()
        orch.start()
        mid = uuid.uuid4()
        r1 = orch.run_cycle(mid, 100.0)
        r2 = orch.run_cycle(mid, 101.0)
        assert r1.timestamp <= r2.timestamp
        orch.stop()
