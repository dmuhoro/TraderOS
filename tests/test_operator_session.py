from __future__ import annotations

import os
import uuid
from unittest.mock import Mock

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.collectors.base import CollectorType
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.operator_session import OperatorSessionService
from traderos.domain.services.operator_workflow import OPERATOR_STEPS
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.operator_workflow import WorkflowError
from traderos.domain.services.operator_workflow import WorkflowStatus
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.strategy_management import StrategyCatalogService
from traderos.infrastructure.repositories.in_memory import InMemoryBacktestResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryOperatorWorkflowRepository
from traderos.infrastructure.repositories.in_memory import InMemoryStrategyRepository


class _BrokerStub(BrokerAdapter):
    balance = 10000.0
    reachable = True

    def place_market_order(self, market_id, side, quantity, close_price=None):
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def get_account_balance(self):
        if not self.reachable:
            raise RuntimeError("connection refused")
        return self.balance

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


def _preflight(ok: bool = True) -> PreflightService:
    audit = Mock()
    audit.verify_chain.return_value = ok
    reconciliation = Mock()
    reconciliation.can_accept_orders = ok
    kill_switch = KillSwitch()
    if not ok:
        kill_switch.record_failure()
        kill_switch.record_failure()
        kill_switch.record_failure()
        kill_switch.record_failure()
        kill_switch.record_failure()
    return PreflightService(
        audit=audit,
        broker_reconciliation=reconciliation,
        kill_switch=kill_switch,
    )


def _catalog() -> StrategyCatalogService:
    return StrategyCatalogService(
        repo=InMemoryStrategyRepository(),
        backtest=BacktestingService(execution=ExecutionService()),
        backtest_results=InMemoryBacktestResultRepository(),
    )


def _paper() -> PaperTradingService:
    portfolio = Mock()
    portfolio.compute_pnl.return_value = 0.0
    return PaperTradingService(
        broker=_BrokerStub(),
        signal_service=Mock(),
        risk_service=Mock(),
        portfolio_service=portfolio,
        execution=ExecutionService(),
    )


def _data_ingestion(sources: int = 1) -> DataIngestionService:
    ingestion = DataIngestionService(registry=Mock())
    for i in range(sources):
        ingestion.add_source(uuid.uuid4(), f"SYM{i}", CollectorType.MOCK)
    return ingestion


def _service(**kwargs) -> tuple[OperatorSessionService, InMemoryOperatorWorkflowRepository]:
    repo = InMemoryOperatorWorkflowRepository()
    defaults: dict = {
        "workflow": OperatorWorkflow(),
        "repository": repo,
        "preflight": _preflight(ok=True),
        "broker": _BrokerStub(),
        "broker_reconciliation": Mock(can_accept_orders=True),
        "data_ingestion": _data_ingestion(),
        "paper": _paper(),
        "strategy_catalog": _catalog(),
    }
    defaults.update(kwargs)
    service = OperatorSessionService(**defaults)
    service.strategy_catalog.ensure_seeded()
    return service, repo


class TestOperatorSessionService:
    def test_full_session_advances_through_all_steps(self) -> None:
        os.environ["LIVE_TRADING_CONFIRMED"] = "true"
        try:
            service, repo = _service()
            for step in OPERATOR_STEPS:
                context = (
                    {"strategy": "mean_reversion"}
                    if step == OperatorStep.STRATEGY_PROMOTION
                    else {}
                )
                outcome = service.perform(step, actor="operator", **context)
                assert outcome.ok, f"{step.value}: {outcome.result}"
                assert service.current_step == step
            assert service.status == WorkflowStatus.COMPLETED
            assert service.session_id is not None
            assert len(service.history()) == len(OPERATOR_STEPS)
            loaded = repo.load()
            assert loaded is not None
            assert loaded.current_step == OperatorStep.SESSION_REPORT
            assert loaded.status == WorkflowStatus.COMPLETED
            assert len(loaded.transitions) == len(OPERATOR_STEPS)
        finally:
            os.environ.pop("LIVE_TRADING_CONFIRMED", None)

    def test_failing_preflight_does_not_advance(self) -> None:
        service, _ = _service(preflight=_preflight(ok=False))
        service.perform(OperatorStep.START)
        outcome = service.perform(OperatorStep.PREFLIGHT)
        assert not outcome.ok
        assert "failed" in outcome.result
        assert service.current_step == OperatorStep.START

    def test_preflight_can_be_rerun_after_failure(self) -> None:
        service, _ = _service(preflight=_preflight(ok=False))
        service.perform(OperatorStep.START)
        failed = service.perform(OperatorStep.PREFLIGHT)
        assert not failed.ok
        service.preflight = _preflight(ok=True)
        passed = service.perform(OperatorStep.PREFLIGHT)
        assert passed.ok
        assert service.current_step == OperatorStep.PREFLIGHT

    def test_failing_broker_check_does_not_advance(self) -> None:
        broker = _BrokerStub()
        broker.reachable = False
        service, _ = _service(broker=broker, broker_reconciliation=None)
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        outcome = service.perform(OperatorStep.BROKER_CHECK)
        assert not outcome.ok
        assert "unreachable" in outcome.result
        assert service.current_step == OperatorStep.PREFLIGHT

    def test_broker_check_requires_reconciliation(self) -> None:
        service, _ = _service(broker_reconciliation=Mock(can_accept_orders=False))
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        outcome = service.perform(OperatorStep.BROKER_CHECK)
        assert not outcome.ok
        assert "reconciliation" in outcome.result

    def test_market_data_check_requires_sources(self) -> None:
        service, _ = _service(data_ingestion=_data_ingestion(sources=0))
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        service.perform(OperatorStep.BROKER_CHECK)
        outcome = service.perform(OperatorStep.MARKET_DATA_CHECK)
        assert not outcome.ok
        assert "no market data sources" in outcome.result

    def test_out_of_order_step_raises_workflow_error(self) -> None:
        service, _ = _service()
        service.perform(OperatorStep.START)
        try:
            service.perform(OperatorStep.PAPER_TRADING)
        except WorkflowError:
            pass
        else:
            raise AssertionError("expected WorkflowError")

    def test_strategy_promotion_requires_name(self) -> None:
        service, _ = _service()
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        service.perform(OperatorStep.BROKER_CHECK)
        service.perform(OperatorStep.MARKET_DATA_CHECK)
        service.perform(OperatorStep.PAPER_TRADING)
        service.perform(OperatorStep.PERFORMANCE_REVIEW)
        outcome = service.perform(OperatorStep.STRATEGY_PROMOTION)
        assert not outcome.ok
        assert "no strategy name" in outcome.result

    def test_strategy_promotion_rejects_unknown(self) -> None:
        service, _ = _service()
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        service.perform(OperatorStep.BROKER_CHECK)
        service.perform(OperatorStep.MARKET_DATA_CHECK)
        service.perform(OperatorStep.PAPER_TRADING)
        service.perform(OperatorStep.PERFORMANCE_REVIEW)
        outcome = service.perform(OperatorStep.STRATEGY_PROMOTION, strategy="nope")
        assert not outcome.ok
        assert "rejected" in outcome.result
        assert service.current_step == OperatorStep.PERFORMANCE_REVIEW

    def test_controlled_live_requires_confirmation(self) -> None:
        os.environ.pop("LIVE_TRADING_CONFIRMED", None)
        service, _ = _service()
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        service.perform(OperatorStep.BROKER_CHECK)
        service.perform(OperatorStep.MARKET_DATA_CHECK)
        service.perform(OperatorStep.PAPER_TRADING)
        service.perform(OperatorStep.PERFORMANCE_REVIEW)
        service.perform(OperatorStep.STRATEGY_PROMOTION, strategy="mean_reversion")
        outcome = service.perform(OperatorStep.CONTROLLED_LIVE)
        assert not outcome.ok
        assert "live preflight failed" in outcome.result

    def test_shutdown_stops_running_paper_sessions(self) -> None:
        os.environ["LIVE_TRADING_CONFIRMED"] = "true"
        try:
            paper = _paper()
            session = paper.create_session(uuid.uuid4(), [uuid.uuid4()])
            paper.start_session(session.id)
            service, _ = _service(paper=paper)
            service.perform(OperatorStep.START)
            service.perform(OperatorStep.PREFLIGHT)
            service.perform(OperatorStep.BROKER_CHECK)
            service.perform(OperatorStep.MARKET_DATA_CHECK)
            service.perform(OperatorStep.PAPER_TRADING)
            service.perform(OperatorStep.PERFORMANCE_REVIEW)
            service.perform(OperatorStep.STRATEGY_PROMOTION, strategy="mean_reversion")
            service.perform(OperatorStep.CONTROLLED_LIVE)
            outcome = service.perform(OperatorStep.SHUTDOWN)
            assert outcome.ok
            assert outcome.detail["sessions_stopped"] == 1
            assert paper.list_sessions()[0].status.value == "stopped"
        finally:
            os.environ.pop("LIVE_TRADING_CONFIRMED", None)

    def test_performance_review_records_ranking(self) -> None:
        service, _ = _service()
        service.perform(OperatorStep.START)
        service.perform(OperatorStep.PREFLIGHT)
        service.perform(OperatorStep.BROKER_CHECK)
        service.perform(OperatorStep.MARKET_DATA_CHECK)
        service.perform(OperatorStep.PAPER_TRADING)
        outcome = service.perform(OperatorStep.PERFORMANCE_REVIEW)
        assert outcome.ok
        assert len(outcome.detail["ranking"]) == 3

    def test_start_reuses_existing_session_id(self) -> None:
        service, _ = _service()
        outcome = service.perform(OperatorStep.START, session_id="sess-1")
        assert outcome.ok
        assert service.session_id == "sess-1"
