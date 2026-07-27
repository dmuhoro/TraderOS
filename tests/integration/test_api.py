from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from traderos.interfaces.api import server

importlib.reload(server)


@pytest.fixture(autouse=True)
def _clean_state():
    server._orch_cache.clear()
    server._api_key = None
    yield


def _make_client(**overrides):
    from fastapi.testclient import TestClient

    app = server.build_app()
    return TestClient(app)


class TestApiHealth:
    def test_get_health(self):
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_get_health_with_api_key(self):
        with patch.object(server, "_load_api_key", return_value="secret123"):
            client = _make_client()
            resp = client.get("/health")
            assert resp.status_code == 401

            resp = client.get("/health", headers={"X-API-Key": "secret123"})
            assert resp.status_code == 200


class TestApiStrategies:
    def test_list_strategies(self):
        resp = _make_client().get("/strategies")
        assert resp.status_code == 200
        assert "strategies" in resp.json()

    def test_get_strategy_not_found(self):
        resp = _make_client().get("/strategies/nonexistent")
        assert resp.status_code == 404

    def test_get_strategy_found(self):
        resp = _make_client().get("/strategies/mean_reversion")
        assert resp.status_code == 200
        assert resp.json()["name"] == "mean_reversion"


class TestApiOrchestrator:
    def test_orchestrator_start_stop(self):
        client = _make_client()
        resp = client.post("/orchestrator/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        resp = client.post("/orchestrator/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_orchestrator_status(self):
        resp = _make_client().get("/orchestrator/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "paper"
        assert "running" in data
        assert "health" in data
        assert "metrics" in data


class TestApiBacktest:
    def test_run_backtest(self):
        resp = _make_client().post("/backtest", json={"strategy": "mean_reversion", "candles": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_return" in data
        assert "sharpe_ratio" in data

    def test_run_backtest_strategy_not_found(self):
        resp = _make_client().post("/backtest", json={"strategy": "Invalid", "candles": 10})
        assert resp.status_code == 404


class TestApiPaperTrade:
    def test_create_paper_session(self):
        resp = _make_client().post("/papertrade/session")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "created"

    def test_list_paper_sessions(self):
        client = _make_client()
        client.post("/papertrade/session")
        resp = client.get("/papertrade/sessions")
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
            resp = _make_client().get("/audit?limit=10")
            assert resp.status_code == 200
            assert "entries" in resp.json()

    def test_get_audit_with_api_key(self):
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", log_level="CRITICAL")
        with (
            patch.object(server, "_load_api_key", return_value="secret123"),
            patch.object(server, "create_orchestrator") as mock_fn,
        ):
            from traderos.application.factory import build_orchestrator

            orch = build_orchestrator(mode="paper", config=cfg)
            mock_fn.return_value = orch
            client = _make_client()
            resp = client.get("/audit")
            assert resp.status_code == 401

            resp = client.get("/audit", headers={"X-API-Key": "secret123"})
            assert resp.status_code == 200


class TestApiMetrics:
    def test_get_metrics_not_running(self):
        resp = _make_client().get("/metrics")
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
            resp = _make_client().get("/metrics")
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
            resp = _make_client().get("/manifest")
            assert resp.status_code == 200
            assert "runs" in resp.json()
