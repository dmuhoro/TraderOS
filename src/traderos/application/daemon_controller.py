from __future__ import annotations

import signal
import time
import uuid
from collections.abc import Callable
from typing import Any

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
from traderos.domain.ports import AuditPort
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.broker_state_reconciliation_service import BrokerReconciliationResult
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.market_hours_engine import MarketHoursEngine
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.reconciliation_service import ReconciliationResult


class DaemonController:
    def __init__(
        self,
        mode: TradingMode,
        cycle_executor: CycleExecutor,
        event_bus: EventBusPort,
        health: HealthPort,
        audit: AuditPort,
        metrics: MetricsPort,
        notifications: NotificationService,
        run_manifest: ManifestPort,
        data_ingestion: DataIngestionService | None = None,
        market_ids: list[uuid.UUID] | None = None,
        default_cash: float = 10000.0,
        market_hours: MarketHoursEngine | None = None,
        reconciliation: OrderReconciliationService | None = None,
        broker_reconciliation: BrokerStateReconciliationService | None = None,
        kill_switch: Any | None = None,
        pre_cycle_hook: Callable[[], None] | None = None,
        post_cycle_hook: Callable[[], None] | None = None,
    ) -> None:
        self._mode = mode
        self._cycle_executor = cycle_executor
        self._event_bus = event_bus
        self._health = health
        self._audit = audit
        self._metrics = metrics
        self._notifications = notifications
        self._run_manifest = run_manifest
        self._data_ingestion = data_ingestion
        self._market_ids = market_ids or []
        self._default_cash = default_cash
        self._market_hours = market_hours or MarketHoursEngine()
        self._reconciliation = reconciliation or OrderReconciliationService()
        self._broker_reconciliation = broker_reconciliation
        self._kill_switch = kill_switch
        self._pre_cycle_hook = pre_cycle_hook
        self._post_cycle_hook = post_cycle_hook
        self._running = False
        self._crash_recovered = False

    @property
    def mode(self) -> TradingMode:
        return self._mode

    @property
    def running(self) -> bool:
        return self._running

    @property
    def market_ids(self) -> list[uuid.UUID]:
        return self._market_ids

    def start(self) -> None:
        self._running = True
        self._health.report_healthy("orchestrator", "started")
        self._audit.record(
            "orchestrator.start", "system", "orchestrator", f"mode={self._mode.value}"
        )
        self._notifications.info("Orchestrator Started", f"Trading mode: {self._mode.value}")
        self._run_manifest.record("orchestrator", "start", metadata={"mode": self._mode.value})

    def stop(self) -> None:
        self._running = False
        self._health.report_healthy("orchestrator", "stopped")
        self._audit.record("orchestrator.stop", "system", "orchestrator")
        self._notifications.info("Orchestrator Stopped")
        self._run_manifest.record("orchestrator", "stop")

    def _run_startup_reconciliation(
        self,
        local_positions: list[dict] | None = None,
        local_orders: list[dict] | None = None,
    ) -> bool:
        if self._broker_reconciliation is None:
            return True
        self._notifications.info("Startup Reconciliation", "Reconciling broker state")
        result = self._broker_reconciliation.reconcile(
            local_positions=local_positions,
            local_orders=local_orders,
        )
        return self._handle_reconciliation_result(result)

    def _run_periodic_reconciliation(
        self,
        local_positions: list[dict] | None = None,
        local_orders: list[dict] | None = None,
    ) -> None:
        if self._broker_reconciliation is None:
            return
        result = self._broker_reconciliation.reconcile(
            local_positions=local_positions,
            local_orders=local_orders,
        )
        self._handle_reconciliation_result(result)

    def _handle_reconciliation_result(self, result: BrokerReconciliationResult) -> bool:
        if result.errors:
            for err in result.errors:
                self._notifications.warning("Reconciliation", err)
                self._health.report_unhealthy("broker_reconciliation", err)
            if self._kill_switch is not None:
                for _ in result.errors:
                    self._kill_switch.record_failure()
            self._audit.record(
                "reconciliation.error",
                "system",
                "broker_reconciliation",
                "; ".join(result.errors),
            )
            if self._metrics:
                self._metrics.counter("reconciliation.errors", len(result.errors))
            return False

        if result.has_mismatches:
            for m in result.mismatches:
                msg = f"{m.mismatch_type.value}: {m.description}"
                self._notifications.warning("Reconciliation Mismatch", msg)
                self._health.report_unhealthy("broker_reconciliation", msg)
                if self._kill_switch is not None and m.severity >= 2:
                    self._kill_switch.record_failure()
                if m.severity >= 2:
                    self._metrics.counter(f"reconciliation.{m.mismatch_type.value}")
            self._audit.record(
                "reconciliation.mismatch",
                "system",
                "broker_reconciliation",
                "; ".join(f"{m.mismatch_type.value}: {m.description}" for m in result.mismatches),
            )
            if self._metrics:
                self._metrics.counter("reconciliation.mismatches", len(result.mismatches))
            return False

        self._metrics.gauge("reconciliation.matched_positions", result.matched_positions)
        self._metrics.gauge("reconciliation.reconciled_positions", result.reconciled_positions)
        self._health.report_healthy(
            "broker_reconciliation",
            f"matched={result.matched_positions} reconciled={result.reconciled_positions}",
        )
        return True

    def recover_from_crash(
        self,
        local_trades: list | None = None,
        broker_orders_state: list | None = None,
    ) -> ReconciliationResult:
        if self._crash_recovered:
            return ReconciliationResult()
        self._crash_recovered = True
        self._notifications.warning("Crash Recovery", "Running post-crash reconciliation")
        self._audit.record("crash.recovery", "system", "orchestrator", "post-crash reconciliation")
        result = self._reconciliation.reconcile_orders(
            local_trades or [],
            broker_orders_state or [],
        )
        self._health.report_healthy("crash_recovery", f"reconciled={result.reconciled}")
        if self._metrics:
            self._metrics.counter("crash.recovered", 1.0)
        return result

    def _is_market_hours(self, mid: uuid.UUID) -> bool:
        return True

    def _detect_crash(self) -> bool:
        """True when the previous daemon process never recorded a clean stop.

        The durable manifest records ``start`` on boot and ``stop`` on shutdown;
        a last action of ``start`` means the process died mid-run (OT-002).
        In-memory manifests have no history, so this defaults to no crash.
        """
        detect = getattr(self._run_manifest, "detect_unclean_shutdown", None)
        if not callable(detect):
            return False
        try:
            return bool(detect("orchestrator"))
        except Exception:  # noqa: BLE001 — crash detection must never crash
            return False

    def _recover_from_crash(
        self,
        local_trades: list | None = None,
        broker_orders_state: list | None = None,
    ) -> ReconciliationResult:
        if self._crash_recovered:
            return ReconciliationResult()
        if not self._detect_crash():
            return ReconciliationResult()
        return self.recover_from_crash(local_trades, broker_orders_state)

    def run_forever(self, interval_seconds: int = 60, shutdown_timeout: int = 30) -> None:
        self._recover_from_crash()
        if not self._run_startup_reconciliation():
            self._notifications.critical(
                "Startup Reconciliation Failed",
                "Broker state reconciliation failed at startup. Trading blocked.",
            )
        self.start()
        shutdown_at: float | None = None
        shutdown_graceful_done = False

        def handle_stop(signum: int, frame: object | None = None) -> None:
            nonlocal shutdown_at, shutdown_graceful_done
            if shutdown_graceful_done:
                return
            self._notifications.warning(
                "Shutdown", "Received stop signal, shutting down gracefully"
            )
            self.stop()
            shutdown_at = time.monotonic() + shutdown_timeout
            shutdown_graceful_done = True

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        while self._running:
            if shutdown_at is not None and time.monotonic() > shutdown_at:
                self._notifications.critical("Shutdown", "Forced shutdown after timeout")
                break
            if (
                self._broker_reconciliation is not None
                and not self._broker_reconciliation.can_accept_orders
            ):
                self._health.report_unhealthy(
                    "broker_reconciliation",
                    "Startup reconciliation incomplete — order acceptance blocked",
                )
                time.sleep(interval_seconds)
                continue
            if self._pre_cycle_hook:
                self._pre_cycle_hook()
            for mid in self._market_ids:
                if not self._running:
                    break
                try:
                    if self._data_ingestion is not None:
                        close_price = self._data_ingestion.get_latest_close(mid)
                    else:
                        close_price = None
                    if close_price is None:
                        self._notifications.warning(
                            "No Data", f"{mid}: cannot fetch price, skipping cycle"
                        )
                        self._health.report_unhealthy(f"market.{mid}", "no price data")
                        continue
                    result = self._cycle_executor.run(mid, close_price)
                    if result.errors:
                        for err in result.errors:
                            self._notifications.warning("Cycle Error", f"{mid}: {err}")
                except (ValueError, RuntimeError, OSError, ServiceError, InfrastructureError) as e:
                    self._notifications.warning("Cycle Panic", f"{mid}: {e}")
                    self._health.report_unhealthy(f"market.{mid}", str(e))
            self._run_periodic_reconciliation()
            if self._post_cycle_hook:
                self._post_cycle_hook()
            time.sleep(interval_seconds)

    def _drain_open_orders(self) -> None:
        self._audit.record("shutdown.drain_orders", "system", "orchestrator")
        self._notifications.info("Shutdown", "Draining open orders")

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._mode.value,
            "running": self._running,
            "markets": len(self._market_ids),
            "health": self._health.summary(),
            "metrics": self._metrics.snapshot(),
            "crash_recovered": self._crash_recovered,
        }
