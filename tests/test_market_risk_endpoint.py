from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any

import httpx
import pytest

from traderos.domain.services.risk_metrics import decimal_to_wire
from traderos.domain.services.risk_metrics import historical_var
from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.interfaces.api import security
from traderos.interfaces.api import server
from traderos.interfaces.api.market import RISK_MEASUREMENT_SCOPE


class _Ingestion:
    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self.rows = rows

    def fetch_all(self, limit: int = 90) -> dict[str, list[dict[str, str]]]:
        return {symbol: candles[-limit:] if limit else [] for symbol, candles in self.rows.items()}


class _Orchestrator:
    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self.data_ingestion = _Ingestion(rows)


def _rows(*closes: str) -> list[dict[str, str]]:
    return [{"close": close} for close in closes]


def _request(
    app: Any, method: str, path: str, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await asyncio.wait_for(client.request(method, path, headers=headers), timeout=30)

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def _reset_security() -> None:
    security.reset_authenticator()
    server.reset_rate_limiter()
    yield
    security.reset_authenticator()


def _factory_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    orchestrator = _Orchestrator(
        {
            # Close-to-close changes are +10, -20, +10, -5. At 95% the
            # nearest-rank lower tail is the worst change, -20; VaR is 20.
            "SPY": _rows("100", "110", "90", "100", "95"),
            "EMPTY": [],
            "ONE": _rows("100"),
            "SHORT": _rows("100", "110"),
        }
    )
    monkeypatch.setattr(server, "create_orchestrator", lambda *args, **kwargs: orchestrator)
    return server.build_app()


def test_risk_endpoint_returns_exact_decimal_money_through_market_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _factory_app(monkeypatch)
    response = _request(app, "GET", "/v1/research/risk?symbol=SPY")
    assert response.status_code == 200
    body = response.json()
    value = body["historical_var"]["value"]
    assert type(value) is str
    assert value == "20"
    assert re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d*[1-9])?", value)
    assert "e" not in value.lower()
    assert body["measurement_scope"] == RISK_MEASUREMENT_SCOPE
    assert body["historical_var"]["observations"] == 4


def test_risk_refusals_name_empty_single_and_insufficient_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _factory_app(monkeypatch)
    for symbol, reason, observations in (
        ("EMPTY", "empty_series", 0),
        ("ONE", "single_observation", 0),
        ("SHORT", "insufficient_history", 1),
    ):
        response = _request(app, "GET", f"/v1/research/risk?symbol={symbol}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "refused"
        assert body["historical_var"]["value"] is None
        assert body["historical_var"]["reason"] == reason
        assert body["historical_var"]["observations"] == observations
        assert body["measurement_scope"] == RISK_MEASUREMENT_SCOPE


def test_risk_endpoint_requires_credentials_when_authentication_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _factory_app(monkeypatch)
    security.set_authenticator(APIKeyAuthenticator(admin_keys=("secret123",)))

    unauthenticated = _request(app, "GET", "/v1/research/risk?symbol=SPY")
    assert unauthenticated.status_code in (401, 403)

    authenticated = _request(
        app,
        "GET",
        "/v1/research/risk?symbol=SPY",
        headers={"X-API-Key": "secret123"},
    )
    assert authenticated.status_code == 200


def test_risk_openapi_description_documents_per_unit_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _factory_app(monkeypatch)
    response = _request(app, "GET", "/openapi.json")
    assert response.status_code == 200
    description = response.json()["paths"]["/v1/research/risk"]["get"]["description"]
    assert RISK_MEASUREMENT_SCOPE in description


def test_historical_var_arithmetic_uses_exact_decimal_price_changes() -> None:
    result = historical_var([Decimal(100), Decimal(110), Decimal(90), Decimal(100), Decimal(95)])
    # Observed changes: +10, -20, +10, -5; sorted: -20, -5, +10, +10.
    # ceil((1 - 0.95) * 4) selects the first (worst) observation: -20.
    assert result.value == Decimal(20)
    assert decimal_to_wire(result.value) == "20"
