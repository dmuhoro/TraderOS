from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from traderos.application.factory import build_orchestrator
from traderos.application.orchestrator import TradingMode
from traderos.domain.entities.user import UserRole
from traderos.infrastructure.config.config_loader import Config
from traderos.interfaces.api import server

USER_A = "trader1"
PASSWORD = "hunter2secure"


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


def _register_login(client: TestClient, username: str = USER_A) -> str:
    resp = client.post("/v1/retail/register", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    resp = client.post("/v1/retail/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()
    return resp.json()["token"]


class TestRetailAuth:
    def test_register_login_me_flow(self, client: TestClient) -> None:
        token = _register_login(client)
        me = client.get("/v1/retail/me", headers={"X-Session-Token": token})
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["user"]["username"] == USER_A
        assert "risk_profile" in body
        assert body["orders_enabled"] is True

    def test_login_wrong_password_denied(self, client: TestClient) -> None:
        client.post("/v1/retail/register", json={"username": "u", "password": PASSWORD})
        resp = client.post("/v1/retail/login", json={"username": "u", "password": "wrong"})
        assert resp.status_code == 401
        assert "token" not in resp.json()

    def test_unknown_session_denied(self, client: TestClient) -> None:
        me = client.get("/v1/retail/me", headers={"X-Session-Token": "bogus-token"})
        assert me.status_code == 401

    def test_missing_session_denied(self, client: TestClient) -> None:
        assert client.get("/v1/retail/me").status_code == 401

    def test_duplicate_username_conflict(self, client: TestClient) -> None:
        _register_login(client, username="dup")
        resp = client.post("/v1/retail/register", json={"username": "dup", "password": PASSWORD})
        assert resp.status_code == 409

    def test_logout_revokes_session(self, client: TestClient) -> None:
        token = _register_login(client)
        assert client.get("/v1/retail/me", headers={"X-Session-Token": token}).status_code == 200
        resp = client.post("/v1/retail/logout", headers={"X-Session-Token": token})
        assert resp.status_code == 200
        assert client.get("/v1/retail/me", headers={"X-Session-Token": token}).status_code == 401

    def test_register_unknown_role_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/retail/register",
            json={"username": "u", "password": PASSWORD, "role": "superadmin"},
        )
        assert resp.status_code == 422


class TestRetailOrdersFailClosed:
    def test_order_without_session_denied(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/retail/orders",
            json={
                "market_id": str(uuid.uuid4()),
                "side": "buy",
                "quantity": 1.0,
                "close_price": 100.0,
            },
        )
        assert resp.status_code == 401

    def test_order_unknown_trader_blocked(self, client: TestClient) -> None:
        """Trader has no per-user risk profile -> the real risk gate DENIES
        the order (fail-closed) and the API surfaces the block reason."""
        token = _register_login(client)
        resp = client.post(
            "/v1/retail/orders",
            json={
                "market_id": str(uuid.uuid4()),
                "side": "buy",
                "quantity": 5.0,
                "close_price": 100.0,
            },
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 400, resp.text
        assert "Order blocked" in resp.json()["error"]["message"]

    def test_invalid_market_id_422(self, client: TestClient) -> None:
        token = _register_login(client)
        resp = client.post(
            "/v1/retail/orders",
            json={
                "market_id": "not-a-uuid",
                "side": "buy",
                "quantity": 5.0,
                "close_price": 100.0,
            },
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 422


class TestRetailOrderProofRealPath:
    """Proof that retail order entry exercises the REAL submission boundary.

    Allowed orders reach the genuine broker seam exactly once; blocked orders
    never reach it (the per-user risk gate is the only referee).
    """

    def _user_profile(self, user_id: str, mid: uuid.UUID, *, engaged: bool = False) -> tuple:
        # The factory derives per-user allowlisted markets as
        # uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{symbol}") — mirror it so
        # the configured allowlist matches the market used in the order.
        sym = f"mkt-{mid}"
        allowed_mid = uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{sym}")
        cfg = Config(db_path=":memory:")
        object.__setattr__(
            cfg,
            "_raw_settings",
            {
                "risk": {
                    "per_users": [
                        {
                            "user_id": user_id,
                            "engaged": engaged,
                            "max_gross_exposure": 1.0,
                            "max_position_size": 0.5,
                            "max_positions_total": 10,
                            "allowed_markets": [sym],
                        }
                    ]
                }
            },
        )
        orch = build_orchestrator(mode="paper", config=cfg)
        return orch, allowed_mid

    def test_allowed_order_reaches_broker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user_id = str(uuid.uuid4())
        mid = uuid.uuid4()
        orch, allowed_mid = self._user_profile(user_id, mid)
        real = orch.broker.place_market_order
        calls: list = []

        def spy(market_id, side, quantity, close_price=None, client_order_id=None):
            calls.append((market_id, side, quantity, close_price))
            return real(market_id, side, quantity, close_price, client_order_id)

        monkeypatch.setattr(orch.broker, "place_market_order", spy)
        result = orch.submit_retail_order(allowed_mid, "buy", 5.0, 100.0, user_id=user_id)
        assert result.allowed, result.reason
        assert len(calls) == 1

    def test_blocked_order_never_reaches_broker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user_id = str(uuid.uuid4())
        mid = uuid.uuid4()
        orch, allowed_mid = self._user_profile(user_id, mid, engaged=True)
        real = orch.broker.place_market_order
        calls: list[list] = []

        def place(market_id, side, quantity, close_price=None, client_order_id=None):
            calls.append((market_id, side, quantity, close_price))
            return real(market_id, side, quantity, close_price, client_order_id)

        monkeypatch.setattr(orch.broker, "place_market_order", place)
        result = orch.submit_retail_order(allowed_mid, "buy", 5.0, 100.0, user_id=user_id)
        assert not result.allowed
        assert calls == []  # concrete proof: broker seam never invoked
        assert result.reason


class TestRetailHttpBoundary:
    """The retail HTTP surface routes through the SAME orchestrator seams."""

    def _user_profile(self, user_id: str, mid: uuid.UUID, *, engaged: bool = False) -> tuple:
        sym = f"mkt-{mid}"
        allowed_mid = uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{sym}")
        cfg = Config(db_path=":memory:")
        object.__setattr__(
            cfg,
            "_raw_settings",
            {
                "risk": {
                    "per_users": [
                        {
                            "user_id": user_id,
                            "engaged": engaged,
                            "max_gross_exposure": 1.0,
                            "max_position_size": 0.5,
                            "max_positions_total": 10,
                            "allowed_markets": [sym],
                        }
                    ]
                }
            },
        )
        orch = build_orchestrator(mode="paper", config=cfg)
        return orch, allowed_mid

    def _patch_server(self, monkeypatch: pytest.MonkeyPatch, orch) -> TestClient:
        monkeypatch.setattr(server, "create_orchestrator", lambda *a, **k: orch)
        return TestClient(server.build_app())

    def test_http_allowed_order_reaches_broker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user_id = str(uuid.uuid4())
        mid = uuid.uuid4()
        orch, allowed_mid = self._user_profile(user_id, mid)
        # The registered trader's id differs from the configured profile id;
        # remap the real resolver seam so the allowlisted profile applies.
        resolver = orch.risk_service.user_resolver
        real_resolve = resolver.resolve
        trader = orch.account_service.create_user("http-trader", PASSWORD, role=UserRole.OPERATOR)
        assert trader is not None

        def resolve(uid):
            if str(uid) == str(trader.id):
                return real_resolve(user_id)
            return real_resolve(uid)

        monkeypatch.setattr(resolver, "resolve", resolve)

        real = orch.broker.place_market_order
        calls: list = []

        def spy(market_id, side, quantity, close_price=None, client_order_id=None):
            calls.append((str(market_id), side, quantity, close_price))
            return real(market_id, side, quantity, close_price, client_order_id)

        monkeypatch.setattr(orch.broker, "place_market_order", spy)
        client = self._patch_server(monkeypatch, orch)

        resp = client.post(
            "/v1/retail/login", json={"username": "http-trader", "password": PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        resp = client.post(
            "/v1/retail/orders",
            json={
                "market_id": str(allowed_mid),
                "side": "buy",
                "quantity": 5.0,
                "close_price": 100.0,
            },
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        assert body["order_id"] is not None
        assert len(calls) == 1  # the real broker seam was reached exactly once

    def test_http_live_mode_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        orch = build_orchestrator(mode="paper", config=Config(db_path=":memory:"))
        orch.mode = TradingMode.LIVE
        client = self._patch_server(monkeypatch, orch)
        resp = client.post("/v1/retail/register", json={"username": "l", "password": PASSWORD})
        assert resp.status_code == 200, resp.text
        resp = client.post("/v1/retail/login", json={"username": "l", "password": PASSWORD})
        token = resp.json()["token"]
        resp = client.post(
            "/v1/retail/orders",
            json={
                "market_id": str(uuid.uuid4()),
                "side": "buy",
                "quantity": 5.0,
                "close_price": 100.0,
            },
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 403
        assert "paper mode only" in resp.json()["error"]["message"]

    def test_me_no_resolver_reports_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        orch = build_orchestrator(mode="paper", config=Config(db_path=":memory:"))
        orch.risk_service.user_resolver = None
        client = self._patch_server(monkeypatch, orch)
        token = _register_login(client, username="no-resolver")
        me = client.get("/v1/retail/me", headers={"X-Session-Token": token})
        assert me.status_code == 200, me.text
        assert me.json()["risk_profile"] == {"configured": False, "engaged": False}

    def test_me_unconfigured_profile_reports_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = build_orchestrator(mode="paper", config=Config(db_path=":memory:"))
        client = self._patch_server(monkeypatch, orch)
        token = _register_login(client, username="plain")
        me = client.get("/v1/retail/me", headers={"X-Session-Token": token})
        assert me.status_code == 200, me.text
        assert me.json()["risk_profile"] == {"configured": False, "engaged": False}

    def test_me_configured_profile_is_visible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user_id = str(uuid.uuid4())
        orch, _ = self._user_profile(user_id, uuid.uuid4())
        client = self._patch_server(monkeypatch, orch)
        token = _register_login(client, username="plain")
        uid = client.get("/v1/retail/me", headers={"X-Session-Token": token}).json()["user"]["id"]
        resolver = orch.risk_service.user_resolver
        real_resolve = resolver.resolve
        monkeypatch.setattr(
            resolver,
            "resolve",
            lambda candidate: real_resolve(user_id) if str(candidate) == uid else None,
        )
        me = client.get("/v1/retail/me", headers={"X-Session-Token": token})
        assert me.status_code == 200, me.text
        assert me.json()["risk_profile"]["configured"] is True
