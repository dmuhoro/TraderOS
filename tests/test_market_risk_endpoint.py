from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi.testclient import TestClient

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.interfaces.api import market
from traderos.interfaces.api import security

SYMBOL = "RISKTEST"
READ_KEY = "viewer-read-key"


class _Source:
    market_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    symbol = SYMBOL


class _Ingest:
    def __init__(self) -> None:
        self.sources = [_Source()]
        self.rows: list[dict] = []

    def fetch_all(self, limit: int = 90) -> dict[str, list[dict]]:
        return {SYMBOL: self.rows[-limit:]}


class _Orchestrator:
    def __init__(self, ingestion: _Ingest) -> None:
        self.data_ingestion = ingestion


@pytest.fixture()
def risk_client() -> TestClient:
    ingestion = _Ingest()
    app = FastAPI()
    router = APIRouter()
    market.register_market_research_endpoints(router, lambda: _Orchestrator(ingestion))
    app.include_router(router, prefix="/v1")
    security.set_authenticator(APIKeyAuthenticator(viewer_keys=(READ_KEY,)))
    client = TestClient(app)
    client._risk_ingestion = ingestion  # type: ignore[attr-defined]
    yield client
    client.close()
    security.reset_authenticator()
    security.reset_session_resolver()


def _set_prices(risk_client: TestClient, prices: list[int]) -> None:
    ingestion = risk_client._risk_ingestion  # type: ignore[attr-defined]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    ingestion.rows = [
        {
            "timestamp": start + timedelta(hours=index),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1,
        }
        for index, price in enumerate(prices)
    ]


def test_risk_openapi_contract_disclaims_account_and_position_exposure(
    risk_client: TestClient,
) -> None:
    operation = risk_client.app.openapi()["paths"]["/v1/research/risk"]["get"]
    description = operation["description"].lower()
    assert "per-unit candle-price change" in description
    assert "not account exposure" in description
    assert "not portfolio exposure" in description
    assert "not position exposure" in description


def test_risk_endpoint_returns_exact_decimal_money_through_market_router(
    risk_client: TestClient,
) -> None:
    _set_prices(risk_client, [100, 110, 90])
    response = risk_client.get(
        "/v1/research/risk",
        params={"symbol": SYMBOL, "confidence": 0.95, "lookback": 2},
        headers={"X-API-Key": READ_KEY},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == SYMBOL
    assert body["unit"] == "quote_currency_per_one_base_unit"
    assert body["risk_scope"] == (
        "Per-unit candle-price change figures; NOT account exposure, NOT portfolio "
        "exposure, and NOT position exposure."
    )
    assert body["historical_var"]["value"] == "20"
    assert isinstance(body["parametric_var"]["value"], str)
    assert body["drawdown"]["amount"] == "20"
    assert body["drawdown"]["peak_at"] == "2025-01-01T01:00:00+00:00"
    assert body["drawdown"]["trough_at"] == "2025-01-01T02:00:00+00:00"


@pytest.mark.parametrize(
    ("prices", "lookback", "reason"),
    [
        ([], 1, "no_price_observations"),
        ([100], 1, "insufficient_price_observations"),
        ([100, 101], 2, "lookback_exceeds_available_history"),
    ],
)
def test_risk_endpoint_refuses_insufficient_history(
    risk_client: TestClient,
    prices: list[int],
    lookback: int,
    reason: str,
) -> None:
    _set_prices(risk_client, prices)
    response = risk_client.get(
        "/v1/research/risk",
        params={"symbol": SYMBOL, "confidence": 0.95, "lookback": lookback},
        headers={"X-API-Key": READ_KEY},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["reason"] == reason


def test_risk_endpoint_requires_read_authorization(risk_client: TestClient) -> None:
    response = risk_client.get(
        "/v1/research/risk",
        params={"symbol": SYMBOL, "confidence": 0.95, "lookback": 2},
        headers={"X-API-Key": "invalid-viewer-key"},
    )
    assert response.status_code == 401
