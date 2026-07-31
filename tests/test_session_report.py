from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.session_report import SessionReportService
from traderos.interfaces.api import server


@pytest.fixture()
def workflow() -> OperatorWorkflow:
    wf = OperatorWorkflow()
    wf.bind_session("report-session")
    for step in OperatorStep:
        wf.advance(step, actor="operator", result="done")
    return wf


@pytest.fixture()
def portfolio() -> None:
    return None


def _trade_repo() -> list[Trade]:
    return []


class TestSessionReportService:
    def test_generate_without_dependencies(self, workflow: OperatorWorkflow) -> None:
        report = SessionReportService(workflow=workflow).generate()
        assert report.session_id == "report-session"
        assert report.workflow_status == "completed"
        assert report.current_step == "session_report"
        assert len(report.steps) == 10
        assert report.portfolio == {}
        assert report.positions == []
        assert report.strategies == []
        assert report.risk == {"engaged": False}
        assert report.duration_seconds is not None
        assert report.to_dict()["generated_at"] == report.generated_at.isoformat()
        assert "Session Report" in report.to_markdown()

    def test_report_includes_promoted_strategy(self, workflow: OperatorWorkflow) -> None:
        catalog = _make_catalog()
        report = SessionReportService(workflow=workflow, strategy_catalog=catalog).generate()
        names = [s["name"] for s in report.strategies]
        assert "moving_average_trend" in names
        assert "volatility_breakout" in names
        assert report.promoted_strategy == "moving_average_trend"

    def test_report_surfaces_position_and_trade_state(self, workflow: OperatorWorkflow) -> None:
        from traderos.domain.services.portfolio_service import PortfolioService
        from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
        from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository

        trade_repo = InMemoryTradeRepository()
        position_repo = InMemoryPositionRepository()
        market = uuid.uuid4()
        trade_repo.add(
            Trade(
                signal_id=uuid.uuid4(),
                market_id=market,
                side=TradeSide.BUY,
                quantity=10,
                price=100.0,
                status=TradeStatus.FILLED,
            )
        )
        position_repo.add(
            Position(
                market_id=market,
                quantity=10,
                entry_price=100.0,
                current_price=105.0,
                pnl=50.0,
            )
        )
        portfolio = PortfolioService(trade_repo=trade_repo, position_repo=position_repo)
        report = SessionReportService(
            workflow=workflow, portfolio=portfolio, cash=10000.0
        ).generate()
        assert len(report.positions) == 1
        assert report.positions[0]["pnl"] == 50.0
        assert len(report.trades) == 1
        assert report.trades[0]["side"] == "buy"
        assert report.portfolio["total_equity"] == 11050.0


class TestSessionReportEndpoint:
    @pytest.fixture(autouse=True)
    def _isolated_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_PATH", ":memory:")
        server._orch_cache.clear()
        server._api_key = None
        yield
        server._orch_cache.clear()

    @pytest.fixture()
    def client(self) -> TestClient:
        return TestClient(server.build_app())

    def test_report_json_empty_session(self, client: TestClient) -> None:
        resp = client.get("/v1/reports/session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] is None
        assert body["workflow_status"] == "idle"
        assert body["strategies"][0]["status"] == "active"

    def test_report_markdown(self, client: TestClient) -> None:
        resp = client.get("/v1/reports/session?fmt=markdown")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "Session Report" in resp.text


def _make_catalog():
    from traderos.domain.services.backtesting_service import BacktestingService
    from traderos.domain.services.execution_service import ExecutionService
    from traderos.domain.services.strategy_management import StrategyCatalogService
    from traderos.infrastructure.repositories.in_memory import InMemoryBacktestResultRepository
    from traderos.infrastructure.repositories.in_memory import InMemoryStrategyRepository

    catalog = StrategyCatalogService(
        repo=InMemoryStrategyRepository(),
        backtest=BacktestingService(execution=ExecutionService()),
        backtest_results=InMemoryBacktestResultRepository(),
    )
    catalog.ensure_seeded()
    catalog.promote("moving_average_trend")
    return catalog
