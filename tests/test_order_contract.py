"""Proves the orders response model + field normalization over the REAL path.

The value of this file: it does not poke at ``_normalize_order`` in isolation.
It builds the actual paper orchestrator, places a real open limit order through
the broker the API exposes, and asserts that ``GET /v1/orders`` returns the
stable ``quantity``/``order_type``/``status`` contract the dashboard reads —
not the broker's raw ``qty``/``type`` dict.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.interfaces.api import operator
from traderos.interfaces.api import schemas
from traderos.interfaces.api import server


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server.reset_orchestrator()
    yield
    server.reset_orchestrator()


def _real_orchestrator_with_open_order() -> PaperBrokerAdapter:
    """Build the real orchestrator and leave one open PENDING limit order."""
    orch = server.create_orchestrator("paper")
    market = uuid.UUID("12345678-1234-5678-1234-567812345678")
    result = orch.broker.place_limit_order(market, "buy", 2.5, 95.0, close_price=None)
    assert result.filled is False  # not filled -> the order stays open
    return orch


class TestOrdersContractOnRealPath:
    def test_open_order_returns_normalized_shape(self) -> None:
        _real_orchestrator_with_open_order()
        client = TestClient(server.build_app())
        resp = client.get("/v1/orders")
        assert resp.status_code == 200
        body = resp.json()
        assert "trading_user_id" in body
        assert len(body["orders"]) == 1
        order = body["orders"][0]
        # the dashboard-read keys exist with the right types
        assert isinstance(order["quantity"], float)
        assert order["quantity"] == 2.5
        assert isinstance(order["symbol"], str) and order["symbol"]
        assert order["side"] == "buy"
        assert order["order_type"] == "limit"
        assert order["status"] == "open"

    def test_response_model_field_set(self) -> None:
        # The typed contract: exactly the fields app.js consumes, no broker leaks
        fields = {f for f in schemas.OrderItem.model_fields}
        assert fields == {"id", "symbol", "side", "quantity", "order_type", "status"}

    def test_orders_response_model_is_typed_in_openapi(self) -> None:
        client = TestClient(server.build_app())
        openapi = client.get("/openapi.json").json()
        schema = openapi["components"]["schemas"]["OrdersResponse"]
        assert "orders" in schema["properties"]
        assert schema["properties"]["orders"]["type"] == "array"
        assert schema["properties"]["orders"]["items"]["$ref"].endswith("OrderItem")


class TestNormalizeOrder:
    def test_paper_broker_raw_and_legacy_broker_dict_are_normalized(self) -> None:
        # PaperBrokerAdapter._record_order writes qty/type (no status).
        paper_raw = {"id": "abc", "symbol": "BTC-USD", "qty": 3, "side": "buy", "type": "market"}
        normalized = operator._normalize_order(paper_raw)
        assert normalized["id"] == "abc"
        assert normalized["symbol"] == "BTC-USD"
        assert normalized["quantity"] == 3.0
        assert normalized["order_type"] == "market"
        assert normalized["status"] == "open"
        # A legacy broker variant (order_id/market_id) resolves the same way.
        legacy = {"order_id": "abc", "market_id": "BTC-USD", "qty": 3, "type": "market"}
        legacy_normalized = operator._normalize_order(legacy)
        assert legacy_normalized["id"] == "abc"
        assert legacy_normalized["symbol"] == "BTC-USD"
        assert legacy_normalized["quantity"] == 3.0
        assert legacy_normalized["order_type"] == "market"

    def test_empty_raw_tolerated(self) -> None:
        normalized = operator._normalize_order({})
        assert normalized["id"] == ""
        assert normalized["quantity"] == 0.0
        assert normalized["order_type"] == "unknown"
        assert normalized["status"] == "open"


class TestErrorEnvelopeConsistency:
    """Every in-scope error path returns the same {error:{code,message}} shape."""

    @pytest.fixture(autouse=True)
    def _auth_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Configure keys so an invalid/missing key is rejected (401) boundary-side.
        from traderos.infrastructure.auth import APIKeyAuthenticator
        from traderos.interfaces.api import security

        monkeypatch.setenv("API_KEYS", "admin:key-secret-123456")
        security.reset_authenticator()
        security.set_authenticator(
            APIKeyAuthenticator(admin_keys=("key-secret-123456",), operator_keys=(), viewer_keys=())
        )
        yield
        security.reset_authenticator()

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/positions"),
            ("GET", "/v1/orders"),
            ("GET", "/v1/trades"),
            ("GET", "/v1/portfolio"),
            ("GET", "/v1/kill-switch"),
            ("GET", "/v1/readiness"),
            ("GET", "/v1/strategies"),
        ],
    )
    def test_http_exception_envelope(self, method: str, path: str) -> None:
        client = TestClient(server.build_app())
        resp = client.request(method, path, headers={"X-API-Key": "definitely-not-a-key"})
        assert resp.status_code == 401
        body = resp.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}
        assert body["error"]["code"] == 401

    def test_validation_422_uses_same_envelope(self) -> None:
        client = TestClient(server.build_app())
        # trades accepts a limit query param; send a non-integer to force 422
        resp = client.get("/v1/trades?limit=abc", headers={"X-API-Key": "key-secret-123456"})
        assert resp.status_code == 422
        body = resp.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}
        assert body["error"]["code"] == 422
        assert "limit" in body["error"]["message"]

    def test_error_from_domain_is_enveloped(self) -> None:
        client = TestClient(server.build_app())
        # attribution replay with inverted window triggers a business 422
        resp = client.get(
            "/v1/attribution/replay?start=2026-08-09T00:00:00&end=2026-08-08T00:00:00",
            headers={"X-API-Key": "key-secret-123456"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}
