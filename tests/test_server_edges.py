from __future__ import annotations

import sys
import uuid
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.rate_limiter import RateLimiter
from traderos.interfaces.api import security
from traderos.interfaces.api import server

ADMIN_KEY = "secret-key-123456"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    security.reset_authenticator()
    security.reset_session_resolver()
    server._orch_cache.clear()
    yield
    security.reset_authenticator()
    security.reset_session_resolver()
    server._orch_cache.clear()


@pytest.fixture()
def auth_client() -> TestClient:
    security.set_authenticator(
        APIKeyAuthenticator(admin_keys=(ADMIN_KEY,), operator_keys=(), viewer_keys=())
    )
    return TestClient(server.build_app())


def _empty_config() -> Config:
    return Config(db_path=":memory:")


class TestServerResetOrchestrator:
    def test_reset_single_mode_preserves_others(self) -> None:
        server._orch_cache["paper"] = MagicMock()
        server._orch_cache["live"] = MagicMock()
        server.reset_orchestrator("paper")
        assert "paper" not in server._orch_cache
        assert "live" in server._orch_cache


class TestServerCors:
    def test_wildcard_cors_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "*")
        app = server.build_app()
        cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
        assert cors.kwargs["allow_origins"] == ["*"]


class TestServerRateLimit:
    def test_exceeding_limit_returns_429(self, auth_client: TestClient) -> None:
        original = server._rate_limiter
        try:
            server._rate_limiter = RateLimiter(max_requests=1, window_seconds=60.0)
            assert auth_client.get("/v1/healthz").status_code == 200
            denied = auth_client.get("/v1/healthz")
            assert denied.status_code == 429
            assert denied.json()["error"]["message"] == "Rate limit exceeded"
        finally:
            server._rate_limiter = original


class TestServerPrometheusMissing:
    def test_metrics_endpoint_501_without_client(
        self, monkeypatch: pytest.MonkeyPatch, auth_client: TestClient
    ) -> None:
        monkeypatch.setitem(sys.modules, "prometheus_client", None)
        resp = auth_client.get("/metrics")
        assert resp.status_code == 501


class TestServerLoginNoAccount:
    def test_login_501_when_account_service_absent(self, auth_client: TestClient) -> None:
        with patch(
            "traderos.interfaces.api.server.create_orchestrator",
            return_value=MagicMock(spec=[]),
        ):
            resp = auth_client.post("/v1/auth/login", json={"username": "u", "password": "p"})
        assert resp.status_code == 501


class TestServerPaperSession:
    @staticmethod
    def _orch() -> MagicMock:
        session = MagicMock()
        session.id = uuid.uuid4()
        session.status.value = "ACTIVE"
        session.current_capital = 10000.0
        paper = MagicMock()
        paper.create_session.return_value = session
        orch = MagicMock()
        orch.paper = paper
        return orch

    def test_create_with_market_ids(self, auth_client: TestClient) -> None:
        with (
            patch("traderos.interfaces.api.server.create_orchestrator") as mock_create,
            patch("traderos.interfaces.api.server.Config") as mock_config,
        ):
            mock_config.load.return_value = _empty_config()
            mock_create.return_value = self._orch()
            resp = auth_client.post(
                "/v1/papertrade/session",
                json={"market_ids": [str(uuid.uuid4())]},
                headers={"X-API-Key": ADMIN_KEY},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACTIVE"

    def test_create_with_no_symbols_or_ids(self, auth_client: TestClient) -> None:
        with (
            patch("traderos.interfaces.api.server.create_orchestrator") as mock_create,
            patch("traderos.interfaces.api.server.Config") as mock_config,
        ):
            mock_config.load.return_value = _empty_config()
            mock_create.return_value = self._orch()
            resp = auth_client.post("/v1/papertrade/session", headers={"X-API-Key": ADMIN_KEY})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACTIVE"


class TestServerHealthTimeout:
    def test_health_503_when_orchestrator_build_times_out(self, auth_client: TestClient) -> None:
        with patch(
            "traderos.interfaces.api.server.create_orchestrator",
            side_effect=TimeoutError("build exceeded budget"),
        ):
            resp = auth_client.get("/v1/health")
        assert resp.status_code == 503
        assert "not ready" in resp.json()["error"]["message"]


class TestServerSessionResolverNoAccount:
    def test_session_token_denied_when_account_service_absent(
        self, auth_client: TestClient
    ) -> None:
        with patch(
            "traderos.interfaces.api.server.create_orchestrator",
            return_value=MagicMock(spec=[]),
        ):
            resp = auth_client.get("/v1/positions", headers={"X-Session-Token": "any-token"})
        assert resp.status_code == 401
