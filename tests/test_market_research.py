"""WP9 — Market Overview + Research Lab endpoint tests.

Proof exercises the REAL wiring: auth boundary -> orchestrator ->
DataIngestionService / AnalysisService / strategy registry -> response.
Any 200 that hides a silent empty dataset, or a route that lets a bogus
credential or a wrong-role session through, fails these tests.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Timeframe
from traderos.domain.entities.candle import Candle
from traderos.domain.services.analysis_service import AnalysisService
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


@pytest.fixture(scope="function")
def authed(client: TestClient) -> dict[str, str]:
    orch = server.create_orchestrator()
    assert orch.account_service is not None
    from traderos.domain.entities.user import UserRole

    orch.account_service.create_user(OPERATOR_NAME, OPERATOR_PASSWORD, role=UserRole("operator"))
    r = client.post(
        "/v1/auth/login", json={"username": OPERATOR_NAME, "password": OPERATOR_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["token"]}


@pytest.fixture(scope="function")
def viewer(client: TestClient) -> dict[str, str]:
    orch = server.create_orchestrator()
    assert orch.account_service is not None
    from traderos.domain.entities.user import UserRole

    orch.account_service.create_user(VIEWER_NAME, VIEWER_PASSWORD, role=UserRole("viewer"))
    r = client.post("/v1/auth/login", json={"username": VIEWER_NAME, "password": VIEWER_PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["token"]}


def test_market_overview_denies_bogus_session(client: TestClient):
    r = client.get("/v1/market/overview", headers={"X-Session-Token": "bogus"})
    assert r.status_code == 401


def test_market_overview_returns_real_symbols(client: TestClient, authed: dict[str, str]):
    r = client.get("/v1/market/overview", headers=authed)
    assert r.status_code == 200, r.text
    markets = r.json()["markets"]
    assert len(markets) > 0
    for m in markets:
        assert m["last"] > 0
        assert "change_pct" in m
        assert m["state"] in {"uptrend", "downtrend", "range"}
        assert "rsi" in m
        assert "atr" in m


def test_market_symbols(client: TestClient, authed: dict[str, str]):
    r = client.get("/v1/market/symbols", headers=authed)
    assert r.status_code == 200, r.text
    assert len(r.json()["symbols"]) > 0


def test_candles_and_indicators_are_consistent(client: TestClient, authed: dict[str, str]):
    syms = client.get("/v1/market/symbols", headers=authed).json()["symbols"]
    sym = syms[0]

    r = client.get(f"/v1/market/candles?symbol={sym}&limit=60", headers=authed)
    assert r.status_code == 200, r.text
    candles_json = r.json()["candles"]
    assert len(candles_json) == 60

    series = [
        Candle(
            market_id=None,
            ohlcv=OHLCV(
                open=Decimal(str(c["open"])),
                high=Decimal(str(c["high"])),
                low=Decimal(str(c["low"])),
                close=Decimal(str(c["close"])),
                volume=Decimal(str(c["volume"])),
            ),
            timestamp=None,
            timeframe=Timeframe.HOUR_1,
        )
        for c in candles_json
    ]

    ind = client.get(f"/v1/research/indicators?symbol={sym}", headers=authed).json()["indicators"]
    rsi = AnalysisService().compute_rsi(series, window=14)
    assert rsi[-1].value == pytest.approx(ind["rsi14"][-1][1], abs=0.01)
    for key in ("sma20", "sma50", "ema12", "rsi14", "atr14", "bollinger", "stochastics"):
        assert key in ind
    assert set(ind["bollinger"]) == {"upper", "middle", "lower"}
    assert set(ind["stochastics"]) == {"k", "d"}
    assert len(ind["sma20"]) >= 20
    assert len(ind["rsi14"]) >= 14


def test_unknown_symbol_fails_closed(client: TestClient, authed: dict[str, str]):
    r = client.get("/v1/market/candles?symbol=NOTAREALSYMBOL", headers=authed)
    assert r.status_code == 404, r.text


def test_backtest_runs_registered_strategy(client: TestClient, authed: dict[str, str]):
    syms = client.get("/v1/market/symbols", headers=authed).json()["symbols"]
    from traderos.domain.services.strategy_framework import registry

    strategy = registry.list()[0]
    r = client.post(
        "/v1/research/backtest",
        json={"strategy": strategy, "symbol": syms[0]},
        headers=authed,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["strategy"] == strategy
    assert "sharpe_ratio" in b
    assert b["candles"] == 120


def test_backtest_unknown_strategy_404(client: TestClient, authed: dict[str, str]):
    syms = client.get("/v1/market/symbols", headers=authed).json()["symbols"]
    r = client.post(
        "/v1/research/backtest",
        json={"strategy": "NO_SUCH_STRATEGY", "symbol": syms[0]},
        headers=authed,
    )
    assert r.status_code == 404, r.text


def test_observations_create_and_list(client: TestClient, authed: dict[str, str]):
    r = client.post(
        "/v1/research/observations",
        json={"symbol": "TESTX", "content": "wp9 lifecycle", "tags": ["wp9"]},
        headers=authed,
    )
    assert r.status_code == 200, r.text
    obs = r.json()
    assert obs["symbol"] == "TESTX"

    r = client.get("/v1/research/observations", headers=authed)
    assert r.status_code == 200, r.text
    assert any(o["id"] == obs["id"] for o in r.json()["observations"])


def test_observations_deny_bogus_session(client: TestClient):
    assert (
        client.post(
            "/v1/research/observations",
            json={"symbol": "X", "content": "y"},
            headers={"X-Session-Token": "bogus"},
        ).status_code
        == 401
    )


def test_viewer_cannot_observe_but_may_research(client: TestClient, viewer: dict[str, str]):
    syms = client.get("/v1/market/symbols", headers=viewer)
    assert syms.status_code == 200, syms.text
    from traderos.domain.services.strategy_framework import registry

    r = client.post(
        "/v1/research/backtest",
        json={"strategy": registry.list()[0], "symbol": syms.json()["symbols"][0]},
        headers=viewer,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/v1/research/observations",
        json={"symbol": "X", "content": "y"},
        headers=viewer,
    )
    assert r.status_code == 403, r.text
