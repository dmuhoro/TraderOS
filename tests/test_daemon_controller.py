from __future__ import annotations

import os
import signal
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
from traderos.domain.services.broker_state_reconciliation_service import BrokerReconciliationResult
from traderos.domain.services.broker_state_reconciliation_service import MismatchDetail
from traderos.domain.services.broker_state_reconciliation_service import MismatchType
from traderos.domain.services.reconciliation_service import ReconciliationResult
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


class TestDaemonControllerExt:
    def _controller(self, **kw) -> DaemonController:
        defaults = dict(
            mode=TradingMode.PAPER,
            cycle_executor=_make_executor(),
            event_bus=InMemoryEventBus(),
            health=HealthService(),
            audit=AuditService(),
            metrics=MetricsService(),
            notifications=Mock(),
            run_manifest=RunManifestService(),
        )
        defaults.update(kw)
        return DaemonController(**defaults)

    def _stopper(self, controller: DaemonController, delay: float = 0.15) -> threading.Thread:
        t = threading.Thread(
            target=lambda: (time.sleep(delay), setattr(controller, "_running", False)), daemon=True
        )
        t.start()
        return t

    # ---- leading / supervision / failover wiring ----
    def test_leading_with_failover(self) -> None:
        failover = Mock()
        failover.leading = False
        controller = self._controller(failover=failover)
        assert controller.leading is False
        failover.leading = True
        assert controller.leading is True

    def test_start_runs_supervision_checks(self) -> None:
        supervision = Mock()
        controller = self._controller(supervision=supervision)
        controller.start()
        supervision.check_unclean_shutdown.assert_called_once()
        supervision.heartbeat.assert_called_once()
        controller.stop()

    def test_stop_releases_failover_and_marks_clean_shutdown(self) -> None:
        supervision = Mock()
        failover = Mock()
        controller = self._controller(supervision=supervision, failover=failover)
        controller.start()
        controller.stop()
        supervision.mark_clean_shutdown.assert_called_once()
        failover.release.assert_called_once()

    # ---- journal pending ----
    def test_journal_pending_none_without_broker(self) -> None:
        assert self._controller()._journal_pending() is None

    def test_journal_pending_none_without_pending_attr(self) -> None:
        controller = self._controller()
        broker = Mock()
        broker.pending = None
        controller._broker = broker
        assert controller._journal_pending() is None

    def test_journal_pending_returns_items(self) -> None:
        controller = self._controller()
        broker = Mock()
        broker.pending.return_value = [{"order": 1}]
        controller._broker = broker
        assert controller._journal_pending() == [{"order": 1}]

    def test_journal_pending_none_for_empty_items(self) -> None:
        controller = self._controller()
        broker = Mock()
        broker.pending.return_value = []
        controller._broker = broker
        assert controller._journal_pending() is None

    def test_journal_pending_swallows_errors(self) -> None:
        controller = self._controller()
        broker = Mock()
        broker.pending.side_effect = RuntimeError("boom")
        controller._broker = broker
        assert controller._journal_pending() is None

    # ---- startup / periodic reconciliation ----
    def test_run_startup_reconciliation_clean(self) -> None:
        rec = Mock()
        rec.reconcile.return_value = BrokerReconciliationResult(
            matched_positions=3, reconciled_positions=3
        )
        controller = self._controller(broker_reconciliation=rec)
        assert controller._run_startup_reconciliation() is True
        rec.reconcile.assert_called_once()

    def test_run_startup_reconciliation_skipped_without_service(self) -> None:
        assert self._controller()._run_startup_reconciliation() is True

    def test_run_periodic_reconciliation_runs(self) -> None:
        rec = Mock()
        rec.reconcile.return_value = BrokerReconciliationResult()
        controller = self._controller(broker_reconciliation=rec)
        controller._run_periodic_reconciliation()
        rec.reconcile.assert_called_once()

    # ---- reconciliation result handling branches ----
    def test_handle_reconciliation_errors_trips_kill_switch(self) -> None:
        result = BrokerReconciliationResult(errors=["broker unreachable"])
        kill = Mock()
        controller = self._controller(broker_reconciliation=Mock(), kill_switch=kill)
        assert controller._handle_reconciliation_result(result) is False
        kill.record_failure.assert_called_once()
        assert controller._health.get_status("broker_reconciliation") is False

    def test_handle_reconciliation_severe_mismatch(self) -> None:
        result = BrokerReconciliationResult(
            mismatches=[MismatchDetail(MismatchType.QUANTITY_MISMATCH, "qty off", severity=2)]
        )
        kill = Mock()
        controller = self._controller(broker_reconciliation=Mock(), kill_switch=kill)
        assert controller._handle_reconciliation_result(result) is False
        kill.record_failure.assert_called_once()

    def test_handle_reconciliation_warning_mismatch(self) -> None:
        result = BrokerReconciliationResult(
            mismatches=[MismatchDetail(MismatchType.PRICE_MISMATCH, "price off", severity=1)]
        )
        kill = Mock()
        controller = self._controller(broker_reconciliation=Mock(), kill_switch=kill)
        assert controller._handle_reconciliation_result(result) is False
        kill.record_failure.assert_not_called()

    def test_handle_reconciliation_clean_reports_healthy(self) -> None:
        result = BrokerReconciliationResult(matched_positions=2, reconciled_positions=2)
        controller = self._controller(broker_reconciliation=Mock())
        assert controller._handle_reconciliation_result(result) is True
        assert controller._health.get_status("broker_reconciliation") is True

    # ---- crash recovery ----
    def test_recover_from_crash_runs_once(self) -> None:
        recon = Mock()
        recon.reconcile_orders.return_value = ReconciliationResult(reconciled=4)
        controller = self._controller()
        controller._reconciliation = recon
        result = controller.recover_from_crash([1], [2])
        assert result.reconciled == 4
        recon.reconcile_orders.assert_called_once_with([1], [2])
        controller.recover_from_crash([], [])
        recon.reconcile_orders.assert_called_once()

    def test_detect_crash_true_when_manifest_reports_unclean(self) -> None:
        manifest = Mock()
        manifest.detect_unclean_shutdown.return_value = True
        controller = self._controller(run_manifest=manifest)
        assert controller._detect_crash() is True

    def test_detect_crash_false_without_detect_method(self) -> None:
        assert self._controller()._detect_crash() is False

    def test_recover_from_crash_when_detected(self) -> None:
        manifest = Mock()
        manifest.detect_unclean_shutdown.return_value = True
        recon = Mock()
        recon.reconcile_orders.return_value = ReconciliationResult(reconciled=2)
        controller = self._controller(run_manifest=manifest)
        controller._reconciliation = recon
        result = controller._recover_from_crash([], [])
        assert result.reconciled == 2
        recon.reconcile_orders.assert_called_once()
        again = controller._recover_from_crash([], [])
        assert again.reconciled == 0
        recon.reconcile_orders.assert_called_once()

    def test_recover_from_crash_noop_without_detection(self) -> None:
        manifest = Mock()
        manifest.detect_unclean_shutdown.return_value = False
        controller = self._controller(run_manifest=manifest)
        assert controller._recover_from_crash().reconciled == 0

    # ---- run_forever wiring paths ----
    def test_run_forever_calls_pre_and_post_hooks(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        pre = Mock()
        post = Mock()
        controller = self._controller(
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            pre_cycle_hook=pre,
            post_cycle_hook=post,
        )
        t = self._stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert pre.called
        assert post.called

    def test_run_forever_breaks_mid_market_loop(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        executor = Mock(spec=CycleExecutor)

        def _cycle_and_stop(mid, price):
            controller._running = False
            return CycleResult(mid, 0, 0, [], 0.0, TS)

        executor.run.side_effect = _cycle_and_stop
        controller = self._controller(
            cycle_executor=executor,
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4(), uuid.uuid4()],
        )
        controller.run_forever(interval_seconds=1, shutdown_timeout=10)
        assert executor.run.call_count == 1

    def test_run_forever_failover_standby_waits_then_leads(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        failover = Mock()
        counts = {"n": 0}

        def _acquire():
            counts["n"] += 1
            return counts["n"] > 1

        failover.try_acquire_leadership.side_effect = _acquire
        controller = self._controller(
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            failover=failover,
            standby_poll_seconds=0.01,
        )
        t = self._stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert failover.try_acquire_leadership.call_count >= 2
        assert failover.renew.called

    def test_run_forever_beats_supervision_heartbeat(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        supervision = Mock()
        controller = self._controller(
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            supervision=supervision,
        )
        t = self._stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert supervision.heartbeat.called

    def test_run_forever_blocks_cycles_when_startup_reconciliation_fails(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        notifications = Mock()
        rec = Mock()
        rec.reconcile.return_value = BrokerReconciliationResult(errors=["startup failure"])
        rec.can_accept_orders = False
        executor = _make_executor()
        controller = self._controller(
            cycle_executor=executor,
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            broker_reconciliation=rec,
            notifications=notifications,
        )
        t = self._stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        notifications.critical.assert_called()
        assert not executor.run.called
        assert controller._health.get_status("broker_reconciliation") is False

    def test_run_forever_proceeds_when_reconciliation_accepts(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        rec = Mock()
        rec.reconcile.return_value = BrokerReconciliationResult(
            matched_positions=1, reconciled_positions=1
        )
        rec.can_accept_orders = True
        executor = _make_executor()
        controller = self._controller(
            cycle_executor=executor,
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            broker_reconciliation=rec,
        )
        t = self._stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert executor.run.called
        rec.reconcile.assert_called()

    def test_run_forever_handles_stop_signal(self) -> None:
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        notifications = Mock()
        controller = self._controller(
            data_ingestion=data_ingestion,
            market_ids=[uuid.uuid4()],
            notifications=notifications,
        )

        def _send_signals():
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGTERM)

        t = threading.Thread(target=_send_signals, daemon=True)
        t.start()
        try:
            controller.run_forever(interval_seconds=1.0, shutdown_timeout=10)
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, signal.default_int_handler)
        t.join(timeout=2)
        assert not controller.running
        assert any(
            "Received stop signal" in str(call) for call in notifications.warning.call_args_list
        )
