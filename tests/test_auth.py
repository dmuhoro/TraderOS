from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.auth import Permission
from traderos.infrastructure.auth import Role
from traderos.interfaces.api import security
from traderos.interfaces.api import server

ADMIN_KEY = "admin-secret-key-123456"
OPERATOR_KEY = "operator-secret-key-123"
VIEWER_KEY = "viewer-secret-key-1234"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server._orch_cache.clear()
    security.reset_authenticator()
    yield
    server._orch_cache.clear()
    security.reset_authenticator()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.build_app())


class TestAPIKeyAuthenticator:
    def test_disabled_when_no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRADEROS_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_ADMIN_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_OPERATOR_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_VIEWER_API_KEY", raising=False)
        auth = APIKeyAuthenticator.from_env()
        assert auth.enabled is False
        assert auth.role_for_key("anything") is None
        # open mode authorizes everyone at the highest privilege
        assert auth.authorize("anything", Permission.ADMIN) is Role.ADMIN

    def test_role_for_key_and_hierarchy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADEROS_ADMIN_API_KEY", ADMIN_KEY)
        monkeypatch.setenv("TRADEROS_OPERATOR_API_KEY", OPERATOR_KEY)
        monkeypatch.setenv("TRADEROS_VIEWER_API_KEY", VIEWER_KEY)
        auth = APIKeyAuthenticator.from_env()
        assert auth.enabled is True
        assert auth.role_for_key(ADMIN_KEY) is Role.ADMIN
        assert auth.role_for_key(OPERATOR_KEY) is Role.OPERATOR
        assert auth.role_for_key(VIEWER_KEY) is Role.VIEWER
        assert auth.role_for_key("wrong") is None
        assert auth.role_for_key(None) is None

    def test_legacy_key_is_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRADEROS_ADMIN_API_KEY", raising=False)
        monkeypatch.setenv("TRADEROS_API_KEY", ADMIN_KEY)
        auth = APIKeyAuthenticator.from_env()
        assert auth.role_for_key(ADMIN_KEY) is Role.ADMIN

    def test_permissions_follow_role_rank(self) -> None:
        auth = APIKeyAuthenticator(
            admin_keys=(ADMIN_KEY,), operator_keys=(OPERATOR_KEY,), viewer_keys=(VIEWER_KEY,)
        )
        assert auth.authorize(VIEWER_KEY, Permission.READ) is Role.VIEWER
        assert auth.authorize(VIEWER_KEY, Permission.OPERATE) is None
        assert auth.authorize(VIEWER_KEY, Permission.ADMIN) is None
        assert auth.authorize(OPERATOR_KEY, Permission.READ) is Role.OPERATOR
        assert auth.authorize(OPERATOR_KEY, Permission.OPERATE) is Role.OPERATOR
        assert auth.authorize(OPERATOR_KEY, Permission.ADMIN) is None
        assert auth.authorize(ADMIN_KEY, Permission.READ) is Role.ADMIN
        assert auth.authorize(ADMIN_KEY, Permission.OPERATE) is Role.ADMIN
        assert auth.authorize(ADMIN_KEY, Permission.ADMIN) is Role.ADMIN
        assert auth.authorize(None, Permission.READ) is None
        assert auth.authorize("nope", Permission.READ) is None

    def test_short_keys_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADEROS_ADMIN_API_KEY", "short")
        auth = APIKeyAuthenticator.from_env()
        assert auth.enabled is False


class TestApiAuthOpenByDefault:
    def test_reads_open_when_no_keys(self, client: TestClient) -> None:
        assert client.get("/v1/portfolio").status_code == 200
        assert client.get("/v1/workflow").status_code == 200

    def test_writes_open_when_no_keys(self, client: TestClient) -> None:
        resp = client.post("/v1/orchestrator/start")
        assert resp.status_code in (200, 400)

    def test_auth_me_reports_not_required(self, client: TestClient) -> None:
        body = client.get("/v1/auth/me").json()
        assert body["required"] is False
        assert body["authenticated"] is False


class TestApiAuthEnforced:
    @pytest.fixture(autouse=True)
    def _keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADEROS_ADMIN_API_KEY", ADMIN_KEY)
        monkeypatch.setenv("TRADEROS_OPERATOR_API_KEY", OPERATOR_KEY)
        monkeypatch.setenv("TRADEROS_VIEWER_API_KEY", VIEWER_KEY)

    def test_missing_key_401(self, client: TestClient) -> None:
        assert client.get("/v1/portfolio").status_code == 401
        assert client.get("/v1/strategies").status_code == 401

    def test_invalid_key_401(self, client: TestClient) -> None:
        resp = client.get("/v1/portfolio", headers={"X-API-Key": "totally-wrong-key"})
        assert resp.status_code == 401

    def test_viewer_reads_but_cannot_operate(self, client: TestClient) -> None:
        h = {"X-API-Key": VIEWER_KEY}
        assert client.get("/v1/portfolio", headers=h).status_code == 200
        assert (
            client.post("/v1/workflow/advance", json={"step": "start"}, headers=h).status_code
            == 403
        )
        assert client.post("/v1/orchestrator/start", headers=h).status_code == 403
        assert client.post("/v1/kill-switch/engage", headers=h).status_code == 403

    def test_operator_operates_but_not_admin(self, client: TestClient) -> None:
        h = {"X-API-Key": OPERATOR_KEY}
        assert client.get("/v1/portfolio", headers=h).status_code == 200
        advance = client.post("/v1/workflow/advance", json={"step": "start"}, headers=h)
        assert advance.status_code == 200
        assert client.post("/v1/orchestrator/start", headers=h).status_code == 403
        assert client.post("/v1/kill-switch/engage", headers=h).status_code == 403

    def test_admin_can_do_everything(self, client: TestClient) -> None:
        h = {"X-API-Key": ADMIN_KEY}
        assert client.get("/v1/portfolio", headers=h).status_code == 200
        assert client.post("/v1/orchestrator/start", headers=h).status_code == 200
        assert client.post("/v1/orchestrator/stop", headers=h).status_code == 200
        assert client.post("/v1/kill-switch/engage", headers=h).status_code == 200

    def test_health_and_metrics_stay_open(self, client: TestClient) -> None:
        assert client.get("/v1/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200

    def test_auth_me_reports_role(self, client: TestClient) -> None:
        body = client.get("/v1/auth/me", headers={"X-API-Key": OPERATOR_KEY}).json()
        assert body["required"] is True
        assert body["authenticated"] is True
        assert body["role"] == "operator"

    def test_auth_me_without_key_reports_unauthenticated(self, client: TestClient) -> None:
        body = client.get("/v1/auth/me").json()
        assert body["required"] is True
        assert body["authenticated"] is False
        assert body["role"] is None
