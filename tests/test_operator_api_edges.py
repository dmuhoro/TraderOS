from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from traderos.application.orchestrator import TradingMode
from traderos.domain.entities.position import Position
from traderos.interfaces.api import operator
from traderos.interfaces.api import server


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server._orch_cache.clear()
    server._api_key = None
    yield
    server._orch_cache.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.build_app())


def _orch() -> object:
    return server.create_orchestrator("paper")


class TestLiveCash:
    def test_balance_success(self, client: TestClient) -> None:
        orch = _orch()
        orch.mode = TradingMode.LIVE
        orch.broker = SimpleNamespace(get_account_balance=lambda: 4321.5)
        body = client.get("/v1/equity-curve").json()
        assert body["points"][-1]["equity"] == 4321.5

    def test_balance_failure_falls_back_to_default_cash(self, client: TestClient) -> None:
        def _raise() -> float:
            raise RuntimeError("broker unreachable")

        orch = _orch()
        orch.mode = TradingMode.LIVE
        orch.broker = SimpleNamespace(get_account_balance=_raise)
        body = client.get("/v1/equity-curve").json()
        assert body["points"][-1]["equity"] == orch.default_cash


class TestEquityCurveLoop:
    def test_equity_curve_builds_points_from_positions(self, client: TestClient) -> None:
        orch = _orch()
        orch.portfolio_service.position_repo.add(
            Position(
                market_id=uuid.uuid4(),
                quantity=10,
                entry_price=100.0,
                current_price=110.0,
                pnl=100.0,
                realized_pnl=50.0,
                updated_at=datetime(2025, 6, 1, tzinfo=UTC),
            )
        )
        orch.portfolio_service.position_repo.add(
            Position(
                market_id=uuid.uuid4(),
                quantity=5,
                entry_price=50.0,
                current_price=60.0,
                pnl=50.0,
                realized_pnl=25.0,
                updated_at=datetime(2025, 6, 2, tzinfo=UTC),
            )
        )
        body = client.get("/v1/equity-curve").json()
        assert len(body["points"]) == 3


class TestReadiness:
    def test_broker_check_false_on_failure(self, client: TestClient) -> None:
        def _boom() -> float:
            raise RuntimeError("down")

        orch = _orch()
        orch.broker.get_account_balance = _boom
        body = client.get("/v1/readiness").json()
        assert body["checks"]["broker"] is False


class TestWorkflow:
    def test_idle_when_no_session(self, client: TestClient) -> None:
        orch = _orch()
        orch.operator_session = None
        body = client.get("/v1/workflow").json()
        assert body == {"current_step": None, "status": "idle", "history": []}

    def test_advance_carries_session_id(self, client: TestClient) -> None:
        orch = _orch()
        assert orch.operator_session is not None
        resp = client.post(
            "/v1/workflow/advance",
            json={"step": "start", "session_id": "sess-abc", "actor": "operator"},
        )
        assert resp.status_code == 200


class TestProbes:
    def test_probes_broker_and_summary(self, client: TestClient) -> None:
        resp = client.get("/v1/probes/broker")
        assert resp.status_code == 200
        assert "ok" in resp.json()
        summary = client.get("/v1/probes")
        assert summary.status_code == 200
        assert "broker" in summary.json()


class TestOrdersNormalization:
    def test_orders_normalize_heterogeneous_fields(self, client: TestClient) -> None:
        orch = _orch()
        orch.broker.get_open_orders = lambda: [
            {"id": "o1", "symbol": "SPY", "side": "buy", "qty": 10, "type": "limit"},
            {
                "order_id": "o2",
                "market_id": "QQQ",
                "side": "sell",
                "quantity": 5,
                "status": "pending",
            },
        ]
        body = client.get("/v1/orders").json()
        by_id = {o["id"]: o for o in body["orders"]}
        assert by_id["o1"]["quantity"] == 10.0
        assert by_id["o1"]["order_type"] == "limit"
        assert by_id["o2"]["status"] == "pending"


class TestSessionReport:
    def test_session_report_json(self, client: TestClient) -> None:
        resp = client.get("/v1/reports/session")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_session_report_markdown(self, client: TestClient) -> None:
        resp = client.get("/v1/reports/session?fmt=markdown")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")


class TestStrategyLifecycleErrors:
    def test_compare_empty_names_400(self, client: TestClient) -> None:
        resp = client.post("/v1/strategies/compare", json={"names": []})
        assert resp.status_code == 400

    def test_review_unknown_strategy_404(self, client: TestClient) -> None:
        resp = client.get("/v1/strategies/does-not-exist/review")
        assert resp.status_code == 404

    def test_enable_retired_400(self, client: TestClient) -> None:
        orch = _orch()
        orch.strategy_catalog.archive("moving_average_trend")
        resp = client.post("/v1/strategies/moving_average_trend/enable")
        assert resp.status_code == 400

    def test_disable_retired_400(self, client: TestClient) -> None:
        orch = _orch()
        orch.strategy_catalog.archive("mean_reversion")
        resp = client.post("/v1/strategies/mean_reversion/disable")
        assert resp.status_code == 400

    def test_promote_disabled_400(self, client: TestClient) -> None:
        orch = _orch()
        orch.strategy_catalog.disable("volatility_breakout")
        resp = client.post("/v1/strategies/volatility_breakout/promote")
        assert resp.status_code == 400

    def test_archive_success(self, client: TestClient) -> None:
        resp = client.post("/v1/strategies/mean_reversion/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "retired"

    def test_clone_to_existing_name_400(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/strategies/moving_average_trend/clone",
            json={"name": "moving_average_trend"},
        )
        assert resp.status_code == 400


class TestSseKeepaliveContinue:
    @pytest.mark.anyio
    async def test_keepalive_loop_continue(self) -> None:
        from traderos.interfaces.api.events import EventBroker

        broker = EventBroker()
        stream = operator.event_stream(broker, lambda: _SnapshotStub(), wait_timeout=0.05)
        frames: list[str] = []
        async for frame in stream:
            frames.append(frame)
            if len(frames) >= 3:
                break
        assert frames[0].startswith("event: snapshot")
        assert frames[1] == ": keepalive\n\n"
        assert frames[2] == ": keepalive\n\n"


class _SnapshotStub:
    """Minimal orchestrator stand-in; _live_snapshot degrades gracefully."""

    mode = TradingMode.PAPER
    default_cash = 10000.0
