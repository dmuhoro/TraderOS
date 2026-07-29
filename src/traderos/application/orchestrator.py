from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.daemon_controller import DaemonController
from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.ports import AuditPort
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.market_hours_engine import MarketHoursEngine
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService


@dataclass
class TradingOrchestrator:
    mode: TradingMode
    signal_service: SignalService
    risk_service: RiskService
    portfolio_service: PortfolioService
    execution: ExecutionService
    analysis: AnalysisService
    broker: BrokerAdapter
    backtest: BacktestingService
    paper: PaperTradingService | None
    event_bus: EventBusPort
    health: HealthPort
    audit: AuditPort
    metrics: MetricsPort
    notifications: NotificationService
    run_manifest: ManifestPort
    data_ingestion: DataIngestionService | None = None

    market_hours: MarketHoursEngine | None = None
    reconciliation: OrderReconciliationService | None = None
    broker_reconciliation: BrokerStateReconciliationService | None = None

    default_cash: float = float(os.getenv("DEFAULT_CASH", "10000.0"))
    market_ids: list[uuid.UUID] = field(default_factory=list)
    _daemon_controller: DaemonController = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._cycle_executor = CycleExecutor(
            mode=self.mode,
            signal_service=self.signal_service,
            risk_service=self.risk_service,
            portfolio_service=self.portfolio_service,
            execution=self.execution,
            analysis=self.analysis,
            broker=self.broker,
            event_bus=self.event_bus,
            health=self.health,
            audit=self.audit,
            metrics=self.metrics,
            notifications=self.notifications,
            run_manifest=self.run_manifest,
            data_ingestion=self.data_ingestion,
            default_cash=self.default_cash,
        )
        self._daemon_controller = DaemonController(
            mode=self.mode,
            cycle_executor=self._cycle_executor,
            event_bus=self.event_bus,
            health=self.health,
            audit=self.audit,
            metrics=self.metrics,
            notifications=self.notifications,
            run_manifest=self.run_manifest,
            data_ingestion=self.data_ingestion,
            market_ids=self.market_ids,
            default_cash=self.default_cash,
            market_hours=self.market_hours,
            reconciliation=self.reconciliation,
            broker_reconciliation=self.broker_reconciliation,
            kill_switch=self.risk_service.kill_switch,
        )

    @property
    def running(self) -> bool:
        return self._daemon_controller.running

    def start(self) -> None:
        self._daemon_controller.start()

    def stop(self) -> None:
        self._daemon_controller.stop()

    def run_cycle(
        self, market_id: uuid.UUID, close_price: float, candle_time: datetime | None = None
    ) -> CycleResult:
        return self._cycle_executor.run(market_id, close_price, candle_time)

    def run_forever(self, interval_seconds: int = 60, shutdown_timeout: int = 30) -> None:
        self._daemon_controller.run_forever(interval_seconds, shutdown_timeout)

    def get_status(self) -> dict[str, Any]:
        return self._daemon_controller.get_status()
