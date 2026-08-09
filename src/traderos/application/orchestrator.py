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
from traderos.application.models import RetailOrderResult
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.ports import AuditPort
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.repositories.strategy_repository import StrategyRepository
from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.account_service import AccountService
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.flatten_service import FlattenService
from traderos.domain.services.knowledge_graph_service import KnowledgeGraphService
from traderos.domain.services.live_readiness import LiveReadinessService
from traderos.domain.services.market_hours_engine import MarketHoursEngine
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.operator_session import OperatorSessionService
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.research_service import ResearchService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_management import StrategyCatalogService
from traderos.infrastructure.ha_failover import FailoverManager
from traderos.infrastructure.probe_scheduler import ProbeScheduler
from traderos.infrastructure.secrets import SecretRotator
from traderos.infrastructure.supervision import SupervisionService


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
    preflight_service: PreflightService | None = None

    default_cash: float = float(os.getenv("DEFAULT_CASH", "10000.0"))
    market_ids: list[uuid.UUID] = field(default_factory=list)
    _daemon_controller: DaemonController = field(init=False, repr=False)

    strategy_repository: StrategyRepository | None = None
    workflow_repository: OperatorWorkflowRepository | None = None
    operator_workflow: OperatorWorkflow | None = None
    trading_user_id: str | None = None
    strategy_catalog: StrategyCatalogService | None = None
    operator_session: OperatorSessionService | None = None
    account_service: AccountService | None = None
    live_readiness: LiveReadinessService | None = None
    secret_rotator: SecretRotator | None = None
    knowledge_graph: KnowledgeGraphService | None = None
    research: ResearchService | None = None
    flatten_service: FlattenService | None = None
    supervision: SupervisionService | None = None
    failover: FailoverManager | None = None
    streaming_feed: Any | None = None
    standby_poll_seconds: float = 5.0
    probe_scheduler: ProbeScheduler | None = None

    def _pre_cycle_check(self) -> None:
        if self.preflight_service is not None:
            verdict = self.preflight_service.check(live_mode=self.mode == TradingMode.LIVE)
            if not verdict.passed:
                for f in verdict.failures:
                    self.notifications.warning("Preflight", f)

    def __post_init__(self) -> None:
        catalog = self.strategy_catalog
        enabled_strategies = (
            (lambda: [(s.name, s.template or s.name, s.params) for s in catalog.get_enabled()])
            if catalog is not None
            else None
        )
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
            preflight_service=self.preflight_service,
            enabled_strategies=enabled_strategies,
            backtest=self.backtest,
            knowledge_graph=self.knowledge_graph,
            research=self.research,
            flatten_service=self.flatten_service,
            trading_user_id=self.trading_user_id,
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
            pre_cycle_hook=self._pre_cycle_check,
            supervision=self.supervision,
            failover=self.failover,
            standby_poll_seconds=self.standby_poll_seconds,
        )

    @property
    def running(self) -> bool:
        return self._daemon_controller.running

    def start(self) -> None:
        self._daemon_controller.start()
        if self.secret_rotator is not None:
            self.secret_rotator.start()
        if self.streaming_feed is not None:
            self.streaming_feed.start()
        if self.probe_scheduler is not None:
            self.probe_scheduler.start()

    def stop(self) -> None:
        if self.streaming_feed is not None:
            self.streaming_feed.stop()
        if self.secret_rotator is not None:
            self.secret_rotator.stop()
        if self.probe_scheduler is not None:
            self.probe_scheduler.stop()
        self._daemon_controller.stop()

    def run_cycle(
        self, market_id: uuid.UUID, close_price: float, candle_time: datetime | None = None
    ) -> CycleResult:
        return self._cycle_executor.run(market_id, close_price, candle_time)

    def submit_retail_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float,
        *,
        user_id: str | None,
        client_order_id: str | None = None,
    ) -> RetailOrderResult:
        """Retail order entry routed through the SAME risk gate + broker as the
        live cycle (B3). Never bypasses the real submission boundary."""
        return self._cycle_executor.submit_retail_order(
            market_id,
            side,
            quantity,
            close_price,
            user_id=user_id,
            client_order_id=client_order_id,
        )

    def run_forever(self, interval_seconds: int = 60, shutdown_timeout: int = 30) -> None:
        if self.probe_scheduler is not None:
            self.probe_scheduler.start()
        try:
            self._daemon_controller.run_forever(interval_seconds, shutdown_timeout)
        finally:
            if self.probe_scheduler is not None:
                self.probe_scheduler.stop()

    def get_status(self) -> dict[str, Any]:
        status = self._daemon_controller.get_status()
        if self.secret_rotator is not None:
            status["secret_rotation"] = self.secret_rotator.stats
        if self.probe_scheduler is not None:
            status["probes"] = {
                name: {"ok": r.ok, "latency_ms": r.latency_ms, "detail": r.detail}
                for name, r in self.probe_scheduler.latest.items()
            }
        status["operational"] = self._operational_status()
        return status

    def _operational_status(self) -> dict[str, Any]:
        """Read-only operational summary for the dashboard (never fabricated).

        Every field is derived from live runtime state: the durable HA lease
        store, the on-call metrics counters the router itself writes, and the
        configured wrist-watch user. When a subsystem is not configured it is
        reported as such rather than claiming protection it does not provide.
        """
        failover: dict[str, Any] = {"configured": False, "leading": False}
        if self.failover is not None:
            failover = self.failover.status()
            failover["configured"] = True

        oncall = self.notifications.oncall
        return {
            "ha": failover,
            "oncall": {
                "configured": oncall is not None,
                "min_severity": None if oncall is None else oncall.min_severity.value,
                "delivered": int(self.metrics.get_counter("oncall.delivered")),
                "delivery_failed": int(self.metrics.get_counter("oncall.delivery_failed")),
            },
            "trading_user_id": self.trading_user_id,
        }
