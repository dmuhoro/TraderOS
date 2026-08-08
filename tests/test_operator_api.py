from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


class TestOperatorApiBasics:
    def test_healthz(self, client: TestClient) -> None:
        resp = client.get("/v1/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_strategies_list_seeded(self, client: TestClient) -> None:
        resp = client.get("/v1/strategies")
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()["strategies"]]
        assert "moving_average_trend" in names
        assert "volatility_breakout" in names
        assert "mean_reversion" in names

    def test_portfolio_empty(self, client: TestClient) -> None:
        resp = client.get("/v1/portfolio")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_equity"] == 10000.0
        assert body["position_count"] == 0

    def test_positions_trades_orders_pnl_empty(self, client: TestClient) -> None:
        assert client.get("/v1/positions").json()["positions"] == []
        assert client.get("/v1/trades").json()["trades"] == []
        assert client.get("/v1/orders").json()["orders"] == []
        pnl = client.get("/v1/pnl").json()
        assert pnl["total_pnl"] == 0.0

    def test_equity_curve_has_current_point(self, client: TestClient) -> None:
        resp = client.get("/v1/equity-curve")
        assert resp.status_code == 200
        points = resp.json()["points"]
        assert len(points) >= 1
        assert points[-1]["equity"] == 10000.0

    def test_readiness_reports_checks(self, client: TestClient) -> None:
        resp = client.get("/v1/readiness")
        assert resp.status_code == 200
        body = resp.json()
        assert "checks" in body
        assert "preflight" in body["checks"]

    def test_preflight_returns_verdict(self, client: TestClient) -> None:
        resp = client.get("/v1/preflight")
        assert resp.status_code == 200
        assert "passed" in resp.json()
        assert "checks" in resp.json()

    def test_positions_orders_trades_surface_trading_user(self, client: TestClient) -> None:
        """The attribution field is threaded at the response seam so the
        dashboard displays the operator identity — present even when empty."""
        positions = client.get("/v1/positions").json()
        assert "trading_user_id" in positions
        assert positions["positions"] == []
        orders = client.get("/v1/orders").json()
        assert "trading_user_id" in orders
        trades = client.get("/v1/trades").json()
        assert "trading_user_id" in trades
        assert trades["trading_user_id"] == positions["trading_user_id"]

    def test_orchestrator_status_includes_operational(self, client: TestClient) -> None:
        resp = client.get("/v1/orchestrator/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "operational" in body
        ops = body["operational"]
        assert "ha" in ops
        assert "oncall" in ops
        assert "trading_user_id" in ops
        # Test env is unconfigured — must be reported as such, never as protected.
        assert ops["ha"]["configured"] is False
        assert ops["oncall"]["configured"] is False


class TestOperatorApiKillSwitch:
    def test_engage_and_disengage(self, client: TestClient) -> None:
        assert client.post("/v1/kill-switch/engage").json() == {"engaged": True}
        status = client.get("/v1/kill-switch").json()
        assert status["engaged"] is True
        assert status["circuit_open"] is True
        assert client.post("/v1/kill-switch/disengage").json() == {"engaged": False}
        status = client.get("/v1/kill-switch").json()
        assert status["engaged"] is False


class TestOperatorApiWorkflow:
    def test_initial_workflow_state(self, client: TestClient) -> None:
        body = client.get("/v1/workflow").json()
        assert body["current_step"] is None
        assert body["status"] == "idle"
        assert body["next_step"] == "start"

    def test_advance_start_then_preflight(self, client: TestClient) -> None:
        start = client.post("/v1/workflow/advance", json={"step": "start"})
        assert start.status_code == 200
        assert start.json()["ok"] is True
        preflight = client.post("/v1/workflow/advance", json={"step": "preflight"})
        assert preflight.status_code == 200
        # broker reconciliation is incomplete on a fresh in-memory build
        assert preflight.json()["ok"] is False
        body = client.get("/v1/workflow").json()
        assert body["current_step"] == "start"
        assert body["status"] == "running"

    def test_out_of_order_step_rejected(self, client: TestClient) -> None:
        client.post("/v1/workflow/advance", json={"step": "start"})
        resp = client.post("/v1/workflow/advance", json={"step": "paper_trading"})
        assert resp.status_code == 409

    def test_unknown_step_rejected(self, client: TestClient) -> None:
        resp = client.post("/v1/workflow/advance", json={"step": "not_a_step"})
        assert resp.status_code == 400

    def test_dry_run_controlled_live_via_api(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        orch = server.create_orchestrator()
        orch.broker_reconciliation.reconcile()
        for step in (
            "start",
            "preflight",
            "broker_check",
            "market_data_check",
            "paper_trading",
            "performance_review",
        ):
            resp = client.post("/v1/workflow/advance", json={"step": step})
            assert resp.status_code == 200, (step, resp.text)
            assert resp.json()["ok"] is True, (step, resp.text)
        promoted = client.post(
            "/v1/workflow/advance",
            json={"step": "strategy_promotion", "strategy": "mean_reversion"},
        )
        assert promoted.status_code == 200
        assert promoted.json()["ok"] is True
        controlled = client.post(
            "/v1/workflow/advance",
            json={"step": "controlled_live", "dry_run": True},
        )
        assert controlled.status_code == 200
        body = controlled.json()
        assert body["ok"] is True
        assert body["detail"]["dry_run"] is True
        assert body["detail"]["live_execution_enabled"] is False
        assert "dry-run" in body["result"]

    def test_live_check_endpoint(self, client: TestClient) -> None:
        resp = client.get("/v1/live/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        assert body["live_execution_enabled"] is False
        assert "broker_connected" in body["checks"]
        assert body["checks"]["operator_session"] is False
        assert body["ready"] is False


class TestOperatorApiStrategies:
    def test_create_get_enable_disable(self, client: TestClient) -> None:
        created = client.post(
            "/v1/strategies",
            json={"name": "ma_fast", "template": "moving_average_trend", "params": {"fast": 5}},
        )
        assert created.status_code == 200
        assert created.json()["status"] == "draft"

        got = client.get("/v1/strategies/ma_fast")
        assert got.status_code == 200
        assert got.json()["template"] == "moving_average_trend"
        assert got.json()["params"] == {"fast": 5}

        enabled = client.post("/v1/strategies/ma_fast/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "active"

        disabled = client.post("/v1/strategies/ma_fast/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

    def test_create_unknown_template_returns_400(self, client: TestClient) -> None:
        resp = client.post("/v1/strategies", json={"name": "bad", "template": "not_a_template"})
        assert resp.status_code == 400

    def test_duplicate_create_returns_400(self, client: TestClient) -> None:
        client.post("/v1/strategies", json={"name": "x", "template": "mean_reversion"})
        resp = client.post("/v1/strategies", json={"name": "x", "template": "mean_reversion"})
        assert resp.status_code == 400

    def test_missing_strategy_returns_404(self, client: TestClient) -> None:
        assert client.get("/v1/strategies/nope").status_code == 404
        assert client.post("/v1/strategies/nope/enable").status_code == 400

    def test_promote_demotes_and_archive_blocks(self, client: TestClient) -> None:
        resp = client.post("/v1/strategies/mean_reversion/promote")
        assert resp.status_code == 200
        assert resp.json()["status"] == "promoted"
        # promoting a second demotes the first
        resp = client.post("/v1/strategies/moving_average_trend/promote")
        assert resp.status_code == 200
        got = client.get("/v1/strategies/mean_reversion").json()
        assert got["status"] == "active"
        # promoted cannot be archived
        resp = client.post("/v1/strategies/moving_average_trend/archive")
        assert resp.status_code == 400

    def test_clone_and_review(self, client: TestClient) -> None:
        clone = client.post("/v1/strategies/mean_reversion/clone", json={"name": "mr_clone"})
        assert clone.status_code == 200
        assert clone.json()["template"] == "mean_reversion"
        review = client.get("/v1/strategies/mean_reversion/review")
        assert review.status_code == 200
        assert review.json()["status"] == "active"

    def test_compare(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/strategies/compare",
            json={"names": ["moving_average_trend", "volatility_breakout", "mean_reversion"]},
        )
        assert resp.status_code == 200
        assert len(resp.json()["ranking"]) == 3
