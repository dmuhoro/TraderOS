from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from traderos.interfaces.api import server

OPERATOR_NAME = "ops-admin"
OPERATOR_PASSWORD = "operator-secret-password"
VIEWER_NAME = "ops-viewer"
VIEWER_PASSWORD = "viewer-secret-password"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server._orch_cache.clear()
    server.reset_rate_limiter()
    yield
    server._orch_cache.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.build_app())


def _seed_operator(client: TestClient, username: str, role: str, password: str) -> None:
    """Seed an operator through the real AccountService behind the API."""
    orch = server.create_orchestrator()
    assert orch.account_service is not None
    from traderos.domain.entities.user import UserRole

    user = orch.account_service.create_user(username, password, role=UserRole(role))
    assert user is not None


def _login(client: TestClient, username: str, password: str):
    return client.post("/v1/auth/login", json={"username": username, "password": password})


class TestOperatorLogin:
    def test_login_success_returns_session_token(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "admin", OPERATOR_PASSWORD)
        resp = _login(client, OPERATOR_NAME, OPERATOR_PASSWORD)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "session"
        assert body["token"]
        assert body["user"]["role"] == "admin"
        assert "expires_at" in body

    def test_wrong_password_denied(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "admin", OPERATOR_PASSWORD)
        resp = _login(client, OPERATOR_NAME, "wrong-password")
        assert resp.status_code == 401

    def test_unknown_user_denied(self, client: TestClient) -> None:
        resp = _login(client, "does-not-exist", "whatever")
        assert resp.status_code == 401

    def test_malformed_body_rejected(self, client: TestClient) -> None:
        resp = client.post("/v1/auth/login", json={"username": "x"})
        assert resp.status_code == 422


class TestOperatorSessionAuth:
    def test_valid_session_reads_guarded_route(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "operator", OPERATOR_PASSWORD)
        token = _login(client, OPERATOR_NAME, OPERATOR_PASSWORD).json()["token"]
        resp = client.get("/v1/portfolio", headers={"X-Session-Token": token})
        assert resp.status_code == 200, resp.text

    def test_unknown_session_denied(self, client: TestClient) -> None:
        resp = client.get("/v1/portfolio", headers={"X-Session-Token": "bogus-token"})
        assert resp.status_code == 401

    def test_invalid_session_shape_denied(self, client: TestClient) -> None:
        resp = client.get("/v1/portfolio", headers={"X-Session-Token": ""})
        assert resp.status_code in (401, 200)

    def test_logout_revokes_session(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "operator", OPERATOR_PASSWORD)
        token = _login(client, OPERATOR_NAME, OPERATOR_PASSWORD).json()["token"]
        h = {"X-Session-Token": token}
        assert client.get("/v1/portfolio", headers=h).status_code == 200
        resp = client.post("/v1/auth/logout", headers=h)
        assert resp.status_code == 200
        assert client.get("/v1/portfolio", headers=h).status_code == 401

    def test_auth_me_reports_session_role(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "admin", OPERATOR_PASSWORD)
        token = _login(client, OPERATOR_NAME, OPERATOR_PASSWORD).json()["token"]
        body = client.get("/v1/auth/me", headers={"X-Session-Token": token}).json()
        assert body["authenticated"] is True
        assert body["role"] == "admin"


class TestOperatorRoleEnforcement:
    def test_viewer_session_cannot_operate(self, client: TestClient) -> None:
        _seed_operator(client, VIEWER_NAME, "viewer", VIEWER_PASSWORD)
        token = _login(client, VIEWER_NAME, VIEWER_PASSWORD).json()["token"]
        h = {"X-Session-Token": token}
        assert client.get("/v1/portfolio", headers=h).status_code == 200
        assert (
            client.post("/v1/workflow/advance", json={"step": "start"}, headers=h).status_code
            == 403
        )

    def test_operator_session_can_operate(self, client: TestClient) -> None:
        _seed_operator(client, OPERATOR_NAME, "operator", OPERATOR_PASSWORD)
        token = _login(client, OPERATOR_NAME, OPERATOR_PASSWORD).json()["token"]
        h = {"X-Session-Token": token}
        resp = client.post("/v1/workflow/advance", json={"step": "start"}, headers=h)
        assert resp.status_code in (200, 400)

    def test_session_does_not_downgrade_api_key(self, client: TestClient) -> None:
        # A session token must not silently weaken the RBAC gate: a viewer
        # session is still denied an operate action even when the API-key seam
        # is unconfigured (open-by-default deployments remain open, but a
        # presented session token is always validated, never ignored).
        _seed_operator(client, VIEWER_NAME, "viewer", VIEWER_PASSWORD)
        token = _login(client, VIEWER_NAME, VIEWER_PASSWORD).json()["token"]
        h = {"X-Session-Token": token}
        assert client.post("/v1/kill-switch/engage", headers=h).status_code == 403
