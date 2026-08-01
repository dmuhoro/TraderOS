from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from traderos.interfaces.api import security
from traderos.interfaces.api import server

importlib.reload(server)


@pytest.fixture(autouse=True)
def _clean_state():
    server._orch_cache.clear()
    security.reset_authenticator()
    yield
    security.reset_authenticator()


def _make_client(**overrides):
    from fastapi.testclient import TestClient

    app = server.build_app()
    return TestClient(app)


class TestApiHealth:
    def test_get_health(self):
        client = _make_client()
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_get_health_with_api_key(self):
        # /v1/health is a readiness probe for load balancers and stays open even
        # when authentication is enabled, while protected routes demand a key.
        from traderos.infrastructure.auth import APIKeyAuthenticator

        security.set_authenticator(APIKeyAuthenticator(admin_keys=("secret123",)))
        client = _make_client()
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/health", headers={"X-API-Key": "secret123"}).status_code == 200
        assert client.get("/v1/strategies").status_code == 401


class TestApiStrategies:
    def test_list_strategies(self):
        resp = _make_client().get("/v1/strategies")
        assert resp.status_code == 200
        assert "strategies" in resp.json()

    def test_get_strategy_not_found(self):
        resp = _make_client().get("/v1/strategies/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_get_strategy_found(self):
        resp = _make_client().get("/v1/strategies/mean_reversion")
        assert resp.status_code == 200
        assert resp.json()["name"] == "mean_reversion"


class TestApiOrchestrator:
    def test_orchestrator_start_stop(self):
        client = _make_client()
        resp = client.post("/v1/orchestrator/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        resp = client.post("/v1/orchestrator/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_orchestrator_status(self):
        resp = _make_client().get("/v1/orchestrator/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "paper"
        assert "running" in data
        assert "health" in data
        assert "metrics" in data


class TestApiBacktest:
    def test_run_backtest(self):
        resp = _make_client().post(
            "/v1/backtest", json={"strategy": "mean_reversion", "candles": 10}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_return" in data
        assert "sharpe_ratio" in data

    def test_run_backtest_strategy_not_found(self):
        resp = _make_client().post("/v1/backtest", json={"strategy": "Invalid", "candles": 10})
        assert resp.status_code == 404
        assert "error" in resp.json()


class TestApiPaperTrade:
    def test_create_paper_session(self):
        resp = _make_client().post("/v1/papertrade/session")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "created"

    def test_list_paper_sessions(self):
        client = _make_client()
        client.post("/v1/papertrade/session")
        resp = client.get("/v1/papertrade/sessions")
        assert resp.status_code == 200
        assert "sessions" in resp.json()


class TestApiAudit:
    def test_get_audit(self):
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", log_level="CRITICAL")
        with patch.object(server, "create_orchestrator") as mock_fn:
            from traderos.application.factory import build_orchestrator

            orch = build_orchestrator(mode="paper", config=cfg)
            mock_fn.return_value = orch
            orch.start()
            orch.stop()
            resp = _make_client().get("/v1/audit?limit=10")
            assert resp.status_code == 200
            assert "entries" in resp.json()

    def test_get_audit_with_api_key(self):
        from traderos.infrastructure.auth import APIKeyAuthenticator
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", log_level="CRITICAL")
        with patch.object(server, "create_orchestrator") as mock_fn:
            from traderos.application.factory import build_orchestrator

            orch = build_orchestrator(mode="paper", config=cfg)
            mock_fn.return_value = orch
            security.set_authenticator(APIKeyAuthenticator(admin_keys=("secret123",)))
            client = _make_client()
            resp = client.get("/v1/audit")
            assert resp.status_code == 401
            assert "error" in resp.json()

            resp = client.get("/v1/audit", headers={"X-API-Key": "secret123"})
            assert resp.status_code == 200


class TestApiMetrics:
    def test_get_metrics_not_running(self):
        resp = _make_client().get("/v1/metrics")
        assert resp.status_code == 200
        assert "warning" in resp.json()

    def test_get_metrics_running(self):
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", log_level="CRITICAL")
        with patch.object(server, "create_orchestrator") as mock_fn:
            from traderos.application.factory import build_orchestrator

            orch = build_orchestrator(mode="paper", config=cfg)
            mock_fn.return_value = orch
            orch.start()
            resp = _make_client().get("/v1/metrics")
            assert resp.status_code == 200
            assert "metrics" in resp.json()


class TestApiManifest:
    def test_get_manifest(self):
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", log_level="CRITICAL")
        with patch.object(server, "create_orchestrator") as mock_fn:
            from traderos.application.factory import build_orchestrator

            orch = build_orchestrator(mode="paper", config=cfg)
            mock_fn.return_value = orch
            orch.start()
            orch.stop()
            resp = _make_client().get("/v1/manifest")
            assert resp.status_code == 200
            assert "runs" in resp.json()


class TestApiPrometheusMetrics:
    def test_prometheus_metrics_endpoint(self):
        client = _make_client()
        resp = client.get("/metrics")
        assert resp.status_code in (200, 501)


class TestApiRateLimit:
    def test_rate_limit_headers(self):
        client = _make_client()
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert "X-RateLimit-Remaining" in resp.headers
