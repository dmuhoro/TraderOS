from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.daemon_controller import DaemonController
from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.run_manifest import RunManifestService


TS = datetime.now(UTC)


def _make_executor(
    run_result: CycleResult | None = None,
    run_raises: type[Exception] | None = None,
) -> CycleExecutor:
    executor = Mock(spec=CycleExecutor)
    if run_raises:
        executor.run.side_effect = run_raises("executor failure")
    elif run_result:
        executor.run.return_value = run_result
    else:
        executor.run.return_value = CycleResult(uuid.uuid4(), 0, 0, [], 0.0, TS)
    return executor


class TestDaemonController:
    def test_start_updates_health_and_audit(self) -> None:
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
        )
        assert not controller.running
        controller.start()
        assert controller.running
        assert controller._health.get_status("orchestrator") is True
        controller.stop()

    def test_stop_clears_running_flag(self) -> None:
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.LIVE,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
        )
        controller.start()
        assert controller.running
        controller.stop()
        assert not controller.running

    def test_get_status_returns_summary(self) -> None:
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
            market_ids=[uuid.uuid4()],
        )
        controller.start()
        status = controller.get_status()
        assert status["mode"] == "paper"
        assert status["running"] is True
        assert status["markets"] == 1
        assert "health" in status
        assert "metrics" in status
        controller.stop()

    def test_run_forever_executes_cycles_then_stops(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
        )

        def _stop_after_delay():
            time.sleep(0.15)
            controller._running = False

        t = threading.Thread(target=_stop_after_delay, daemon=True)
        t.start()
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert executor.run.called

    def test_run_forever_no_data_ingestion_skips_cycles(self) -> None:
        notifications = Mock()
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=notifications,
            run_manifest=RunManifestService(),
            market_ids=[uuid.uuid4()],
        )

        def _stop_after_delay():
            time.sleep(0.15)
            controller._running = False

        t = threading.Thread(target=_stop_after_delay, daemon=True)
        t.start()
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert not executor.run.called
        notifications.warning.assert_called()

    def test_run_forever_catches_executor_errors(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        executor = _make_executor(run_result=CycleResult(uuid.uuid4(), 0, 0, ["error"], 0.0, TS))
        notifications = Mock()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=notifications,
            run_manifest=RunManifestService(),
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
        )

        def _stop_after_delay():
            time.sleep(0.15)
            controller._running = False

        t = threading.Thread(target=_stop_after_delay, daemon=True)
        t.start()
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        notifications.warning.assert_called()

    def test_run_forever_catches_exceptions_from_executor(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        executor = _make_executor(run_raises=ValueError)
        notifications = Mock()
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=notifications,
            run_manifest=RunManifestService(),
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
        )

        def _stop_after_delay():
            time.sleep(0.15)
            controller._running = False

        t = threading.Thread(target=_stop_after_delay, daemon=True)
        t.start()
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        notifications.warning.assert_called()

    def test_properties(self) -> None:
        mid = uuid.uuid4()
        executor = _make_executor()
        controller = DaemonController(
            mode=TradingMode.BACKTEST,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
            market_ids=[mid],
        )
        assert controller.mode == TradingMode.BACKTEST
        assert controller.market_ids == [mid]
