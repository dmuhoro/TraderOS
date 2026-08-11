from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

import pytest

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
    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
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

    def test_operational_status_defaults(self) -> None:
        """No failover and no on-call configured must be reported as such —
        never as protected. `trading_user_id` is surfaced as configured."""
        orch = self._make()
        orch.notifications.oncall = None
        orch.trading_user_id = "trader-1"
        status = orch.get_status()
        ops = status["operational"]
        assert ops["ha"] == {"configured": False, "leading": False}
        assert ops["oncall"]["configured"] is False
        assert ops["oncall"]["delivered"] == 0
        assert ops["oncall"]["delivery_failed"] == 0
        assert ops["trading_user_id"] == "trader-1"

    def test_operational_status_reflects_live_counters(self) -> None:
        """The on-call summary must read the same metrics counters the router
        writes — a real delivery bumps `delivered`, never fabricated."""
        orch = self._make()
        orch.notifications.oncall = Mock()
        orch.notifications.oncall.min_severity = Mock()
        orch.notifications.oncall.min_severity.value = "critical"
        orch.metrics.counter("oncall.delivered", 1.0)
        orch.metrics.counter("oncall.delivery_failed", 1.0)
        ops = orch.get_status()["operational"]
        assert ops["oncall"]["configured"] is True
        assert ops["oncall"]["min_severity"] == "critical"
        assert ops["oncall"]["delivered"] == 1
        assert ops["oncall"]["delivery_failed"] == 1

    def test_multiple_cycles(self) -> None:
        orch = self._make()
        orch.start()
        mid = uuid.uuid4()
        r1 = orch.run_cycle(mid, 100.0)
        r2 = orch.run_cycle(mid, 101.0)
        assert r1.timestamp <= r2.timestamp
        orch.stop()

    def test_preflight_failure_fans_out_warning(self) -> None:
        from types import SimpleNamespace

        orch = self._make()
        preflight = Mock()
        preflight.check.return_value = SimpleNamespace(passed=False, failures=["db down"])
        orch.preflight_service = preflight
        orch._pre_cycle_check()
        preflight.check.assert_called_once_with(live_mode=False)
        orch.notifications.warning.assert_called_once_with("Preflight", "db down")

    def test_streaming_feed_start_and_stop(self) -> None:
        orch = self._make()
        feed = Mock()
        orch.streaming_feed = feed
        orch.start()
        feed.start.assert_called_once()
        orch.stop()
        feed.stop.assert_called_once()

    def test_run_forever_with_probe_scheduler(self) -> None:
        orch = self._make()
        probes = Mock()
        orch.probe_scheduler = probes
        orch._daemon_controller.run_forever = Mock()
        orch.run_forever(interval_seconds=1, shutdown_timeout=1)
        probes.start.assert_called_once()
        probes.stop.assert_called_once()
        orch._daemon_controller.run_forever.assert_called_once_with(1, 1)

    def test_run_forever_stops_probes_on_daemon_exit(self) -> None:
        orch = self._make()
        probes = Mock()
        orch.probe_scheduler = probes

        def _die(*_args, **_kwargs):
            raise RuntimeError("daemon crashed")

        orch._daemon_controller.run_forever = _die
        with pytest.raises(RuntimeError):
            orch.run_forever(interval_seconds=1, shutdown_timeout=1)
        probes.start.assert_called_once()
        probes.stop.assert_called_once()  # finally always releases the probes

    def test_operational_status_reports_configured_failover(self) -> None:
        orch = self._make()
        failover = Mock()
        failover.status.return_value = {"leading": False, "owner": "primary"}
        orch.failover = failover
        ops = orch.get_status()["operational"]
        assert ops["ha"]["configured"] is True
        assert ops["ha"]["owner"] == "primary"
