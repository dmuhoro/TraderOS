from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as StarletteRequest

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.auth import Permission
from traderos.infrastructure.auth import Role
from traderos.interfaces.api import security


def _request(headers: dict[str, str] | None = None, query: str = "") -> StarletteRequest:
    hdrs: list[tuple[bytes, bytes]] = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    return StarletteRequest(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/v1/portfolio",
            "headers": hdrs,
            "server": ("test", 80),
            "client": ("test", 12345),
            "scheme": "http",
            "query_string": query.encode(),
            "root_path": "",
        }
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADEROS_API_KEY", raising=False)
    monkeypatch.delenv("TRADEROS_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("TRADEROS_OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("TRADEROS_VIEWER_API_KEY", raising=False)
    monkeypatch.delenv("TRADING_MODE", raising=False)
    security.reset_authenticator()
    security.reset_session_resolver()
    yield
    security.reset_authenticator()
    security.reset_session_resolver()


class TestSessionTokenSeam:
    def test_reset_session_resolver_clears(self) -> None:
        security.set_session_resolver(lambda _t: Role.OPERATOR)
        security.reset_session_resolver()
        assert security._session_resolver is None

    def test_current_role_resolves_valid_session(self) -> None:
        security.set_session_resolver(lambda token: Role.OPERATOR if token == "tok" else None)
        req = _request(headers={"X-Session-Token": "tok"})
        assert security.current_role(req) is Role.OPERATOR

    def test_current_role_rejects_invalid_session_token(self) -> None:
        security.set_session_resolver(lambda _token: None)
        req = _request(headers={"X-Session-Token": "expired"})
        with pytest.raises(StarletteHTTPException) as exc:
            security.current_role(req)
        assert exc.value.status_code == 401

    def test_current_role_401_without_key_when_enabled(self) -> None:
        security.set_authenticator(APIKeyAuthenticator(admin_keys=("admin-secret-key",)))
        req = _request()
        with pytest.raises(StarletteHTTPException) as exc:
            security.current_role(req)
        assert exc.value.status_code == 401

    def test_permission_dependency_session_grant(self) -> None:
        security.set_session_resolver(lambda _t: Role.OPERATOR)
        req = _request(headers={"X-Session-Token": "tok"})
        assert security.require_read(req) is Role.OPERATOR
        assert security.require_operate(req) is Role.OPERATOR

    def test_permission_dependency_session_insufficient_403(self) -> None:
        security.set_session_resolver(lambda _t: Role.VIEWER)
        req = _request(headers={"X-Session-Token": "tok"})
        with pytest.raises(StarletteHTTPException) as exc:
            security.require_operate(req)
        assert exc.value.status_code == 403

    def test_permission_dependency_key_insufficient_403(self) -> None:
        security.set_authenticator(
            APIKeyAuthenticator(
                admin_keys=("admin-secret-key",), viewer_keys=("viewer-secret-key",)
            )
        )
        req = _request(headers={"X-API-Key": "viewer-secret-key"})
        with pytest.raises(StarletteHTTPException) as exc:
            security.require_operate(req)
        assert exc.value.status_code == 403

    def test_permission_dependency_key_grant(self) -> None:
        security.set_authenticator(APIKeyAuthenticator(admin_keys=("admin-secret-key",)))
        req = _request(headers={"X-API-Key": "admin-secret-key"})
        assert security.require_admin(req) is None
        with pytest.raises(StarletteHTTPException):
            security.require_operate(_request())
        assert security.require_operate(req) is None


class _FakeAuth:
    def __init__(self, enabled: bool = True, role=None) -> None:
        self.enabled = enabled
        self._role = role

    def role_for_key(self, key: str | None) -> Role | None:
        return self._role

    def authorize(self, key: str | None, permission: Permission) -> Role | None:
        return self._role

    def describe(self) -> dict:
        return {"roles": ["admin", "operator", "viewer"]}


class TestRequireSse:
    def test_open_when_disabled_and_no_session(self) -> None:
        security.set_authenticator(_FakeAuth(enabled=False))
        assert security.require_sse(_request()) is None

    def test_accepts_session_role(self) -> None:
        security.set_session_resolver(lambda _t: Role.OPERATOR)
        assert security.require_sse(_request(headers={"X-Session-Token": "tok"})) is None

    def test_forbids_session_without_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        security.set_session_resolver(lambda _t: Role.VIEWER)
        monkeypatch.setattr(security, "role_grants", lambda role, perm: None)
        with pytest.raises(StarletteHTTPException) as exc:
            security.require_sse(_request(headers={"X-Session-Token": "tok"}))
        assert exc.value.status_code == 403

    def test_accepts_header_key(self) -> None:
        security.set_authenticator(_FakeAuth(role=Role.VIEWER))
        assert security.require_sse(_request(headers={"X-API-Key": "viewer-secret-key"})) is None

    def test_invalid_header_key_401(self) -> None:
        security.set_authenticator(_FakeAuth(role=None))
        with pytest.raises(StarletteHTTPException) as exc:
            security.require_sse(_request(headers={"X-API-Key": "wrong"}))
        assert exc.value.status_code == 401

    def test_key_without_read_grant_403(self) -> None:
        auth = _FakeAuth(role=Role.VIEWER)
        auth.authorize = lambda _k, _p: None  # type: ignore[method-assign]
        security.set_authenticator(auth)
        with pytest.raises(StarletteHTTPException) as exc:
            security.require_sse(_request(headers={"X-API-Key": "viewer-secret-key"}))
        assert exc.value.status_code == 403


class TestAuthInfo:
    def test_reports_session_role(self) -> None:
        security.set_authenticator(_FakeAuth(enabled=True))
        security.set_session_resolver(lambda _t: Role.OPERATOR)
        body = security.auth_info(_request(headers={"X-Session-Token": "tok"}))
        assert body["authenticated"] is True
        assert body["role"] == "operator"

    def test_reports_key_role(self) -> None:
        security.set_authenticator(_FakeAuth(enabled=True, role=Role.VIEWER))
        body = security.auth_info(_request(headers={"X-API-Key": "viewer-secret-key"}))
        assert body["authenticated"] is True
        assert body["role"] == "viewer"


class TestAuthBoundaryEdges:
    def test_non_v1_path_passes_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        req = _request()
        req.scope["path"] = "/metrics"
        assert security.enforce_auth_boundary(req) is None

    def test_public_v1_path_passes_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        req = _request()
        req.scope["path"] = "/v1/healthz"
        assert security.enforce_auth_boundary(req) is None

    def test_session_credential_passes_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        security.set_session_resolver(lambda _t: Role.OPERATOR)
        req = _request(headers={"X-Session-Token": "tok"})
        req.scope["path"] = "/v1/portfolio"
        assert security.enforce_auth_boundary(req) is None

    def test_unauthenticated_v1_path_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        req = _request()
        req.scope["path"] = "/v1/portfolio"
        with pytest.raises(StarletteHTTPException) as exc:
            security.enforce_auth_boundary(req)
        assert exc.value.status_code == 401
