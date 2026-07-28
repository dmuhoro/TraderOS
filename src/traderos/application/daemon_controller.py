from __future__ import annotations

import signal
import time
import uuid
from typing import Any

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.ports import AuditPort
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.exceptions import ServiceError
from traderos.domain.exceptions import InfrastructureError


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
        self._running = False

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

    def run_forever(self, interval_seconds: int = 60, shutdown_timeout: int = 30) -> None:
        self.start()
        shutdown_at: float | None = None

        def handle_stop(signum: int, frame: object | None = None) -> None:
            nonlocal shutdown_at
            self.stop()
            shutdown_at = time.monotonic() + shutdown_timeout

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        while self._running:
            if shutdown_at is not None and time.monotonic() > shutdown_at:
                self._notifications.critical("Shutdown", "Forced shutdown after timeout")
                break
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
            time.sleep(interval_seconds)

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._mode.value,
            "running": self._running,
            "markets": len(self._market_ids),
            "health": self._health.summary(),
            "metrics": self._metrics.snapshot(),
        }
