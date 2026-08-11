from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi.testclient import TestClient

from traderos.domain.services.research_service import Observation
from traderos.interfaces.api import market


class _Source:
    def __init__(self, symbol: str, market_id: uuid.UUID) -> None:
        self.symbol = symbol
        self.market_id = market_id


class _StubIngest:
    def __init__(self, rows_by_symbol: dict[str, list[dict]], sources: list[_Source]) -> None:
        self._rows = rows_by_symbol
        self.sources = sources

    def fetch_all(self, limit: int = 90) -> dict[str, list[dict]]:
        return {s: r[-limit:] for s, r in self._rows.items()}


class _StubResearch:
    def __init__(self) -> None:
        self._observations: list[Observation] = []
        self._fail_create = False

    @property
    def observations(self) -> _StubObservations:
        return _StubObservations(self)

    def create_observation(
        self, symbol: str, content: str, tags: list[str] | None = None
    ) -> Observation:
        if self._fail_create:
            raise ValueError("content must not be empty")
        obs = Observation(
            timestamp=datetime.now(tz=UTC), symbol=symbol, content=content, tags=tags or []
        )
        self._observations.append(obs)
        return obs


class _StubObservations:
    def __init__(self, research: _StubResearch) -> None:
        self._research = research

    def list(self) -> list[Observation]:
        return list(self._research._observations)


def _make_rows(n: int, base: float = 100.0) -> list[dict]:
    rows = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    price = base
    for i in range(n):
        rows.append(
            {
                "timestamp": (start + timedelta(hours=i)).isoformat(),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000,
            }
        )
        price += 0.5
    return rows


def _build_client() -> TestClient:
    mid = uuid.uuid4()
    ingest = _StubIngest(
        rows_by_symbol={
            "SPY": _make_rows(90),
            "SPY-BADTS": _make_rows(5),
            "EMPTY": [],
        },
        sources=[_Source("SPY", mid), _Source("SPY-BADTS", mid)],
    )
    research_svc = _StubResearch()

    class _StubOrch:
        data_ingestion = ingest
        research = research_svc

    app = FastAPI()
    router = APIRouter()
    market.register_market_research_endpoints(router, lambda: _StubOrch())
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _build_client()


class TestMarketOverview:
    def test_overview_lists_symbols(self, client: TestClient) -> None:
        resp = client.get("/market/overview")
        assert resp.status_code == 200
        body = resp.json()["markets"]
        symbols = {r["symbol"] for r in body}
        assert "SPY" in symbols
        assert "SPY-BADTS" in symbols
        spy = next(r for r in body if r["symbol"] == "SPY")
        assert spy["last"] > 0
        assert spy["state"] in ("uptrend", "downtrend", "range")
        assert spy["sma20"] > 0
        assert spy["rsi"] is not None
        assert spy["atr"] is not None

    def test_overview_sorted(self, client: TestClient) -> None:
        symbols = [r["symbol"] for r in client.get("/market/overview").json()["markets"]]
        assert symbols == sorted(symbols)


class TestMarketCandles:
    def test_candles_shape(self, client: TestClient) -> None:
        resp = client.get("/market/candles", params={"symbol": "SPY", "limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert len(body["candles"]) == 10
        assert "close" in body["candles"][0]

    def test_unknown_symbol_404(self, client: TestClient) -> None:
        resp = client.get("/market/candles", params={"symbol": "NOPE"})
        assert resp.status_code == 404

    def test_symbol_without_source_404(self, client: TestClient) -> None:
        rows = _make_rows(3)
        mid = uuid.uuid4()
        ingest = _StubIngest(rows_by_symbol={"LONELY": rows}, sources=[_Source("OTHER", mid)])

        class _Orch:
            data_ingestion = ingest

        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        resp = TestClient(app).get("/market/candles", params={"symbol": "LONELY"})
        assert resp.status_code == 404


class TestMarketSymbols:
    def test_symbols_list(self, client: TestClient) -> None:
        resp = client.get("/market/symbols")
        assert resp.status_code == 200
        assert resp.json()["symbols"] == ["SPY", "SPY-BADTS"]


class TestResearchIndicators:
    def test_indicators_shape(self, client: TestClient) -> None:
        resp = client.get("/research/indicators", params={"symbol": "SPY", "limit": 90})
        assert resp.status_code == 200
        ind = resp.json()["indicators"]
        assert len(ind["sma20"]) > 0
        assert len(ind["sma50"]) > 0
        assert len(ind["ema12"]) > 0
        assert len(ind["rsi14"]) > 0
        assert len(ind["atr14"]) > 0
        assert len(ind["bollinger"]["upper"]) > 0
        assert len(ind["stochastics"]["k"]) > 0


class TestResearchBacktest:
    def test_backtest_runs(self, client: TestClient) -> None:
        resp = client.post(
            "/research/backtest",
            json={"strategy": "moving_average_trend", "symbol": "SPY"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["strategy"] == "moving_average_trend"
        assert body["symbol"] == "SPY"
        assert body["candles"] > 0
        assert "sharpe_ratio" in body

    def test_unknown_strategy_404(self, client: TestClient) -> None:
        resp = client.post(
            "/research/backtest",
            json={"strategy": "not_a_strategy", "symbol": "SPY"},
        )
        assert resp.status_code == 404


class TestResearchObservations:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/research/observations")
        assert resp.status_code == 200
        assert resp.json()["observations"] == []

    def test_create_observation(self, client: TestClient) -> None:
        resp = client.post(
            "/research/observations",
            json={"symbol": "SPY", "content": "gap up on volume", "tags": ["momentum"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["content"] == "gap up on volume"
        listed = client.get("/research/observations").json()["observations"]
        assert len(listed) == 1


class TestMarketErrorPaths:
    def test_bad_timestamp_string_is_none(self) -> None:
        rows = _make_rows(1)
        rows[0]["timestamp"] = "not-a-timestamp"
        mid = uuid.uuid4()
        ingest = _StubIngest(rows_by_symbol={"SPY": rows}, sources=[_Source("SPY", mid)])

        class _Orch:
            data_ingestion = ingest

        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/market/candles", params={"symbol": "SPY"})
        assert resp.status_code == 200
        assert resp.json()["candles"][0]["timestamp"] is None

    def test_create_observation_value_error_400(self) -> None:
        rows = _make_rows(3)
        mid = uuid.uuid4()
        research_svc = _StubResearch()
        research_svc._fail_create = True
        ingest = _StubIngest(rows_by_symbol={"SPY": rows}, sources=[_Source("SPY", mid)])

        class _Orch:
            data_ingestion = ingest
            research = research_svc

        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/research/observations", json={"symbol": "SPY", "content": ""})
        assert resp.status_code == 400

    def test_missing_ingest_503(self) -> None:
        class _Orch:
            data_ingestion = None

        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        client = TestClient(app)
        assert client.get("/market/overview").status_code == 503
        assert client.get("/market/symbols").status_code == 503
        assert client.get("/market/candles", params={"symbol": "SPY"}).status_code == 503

    def test_missing_research_503(self) -> None:
        class _Orch:
            data_ingestion = None
            research = None

        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        client = TestClient(app)
        assert client.get("/research/observations").status_code == 503
        assert (
            client.post(
                "/research/observations", json={"symbol": "SPY", "content": "x"}
            ).status_code
            == 503
        )

    def test_backtest_failure_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = _make_rows(3)
        mid = uuid.uuid4()
        ingest = _StubIngest(rows_by_symbol={"SPY": rows}, sources=[_Source("SPY", mid)])

        class _Orch:
            data_ingestion = ingest

        def _boom(_cls, _candles, _market_id):
            raise RuntimeError("candle gap")

        monkeypatch.setattr(
            "traderos.domain.services.backtesting_service.BacktestingService.run",
            _boom,
        )
        app = FastAPI()
        router = APIRouter()
        market.register_market_research_endpoints(router, lambda: _Orch())
        app.include_router(router)
        client = TestClient(app)
        resp = client.post(
            "/research/backtest",
            json={"strategy": "moving_average_trend", "symbol": "SPY"},
        )
        assert resp.status_code == 400
