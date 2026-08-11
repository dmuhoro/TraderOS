from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from urllib.parse import quote

from fastapi.testclient import TestClient

from traderos.application.factory import build_orchestrator
from traderos.infrastructure.config.config_loader import Config
from traderos.interfaces.api import server


def _profile_config(user_id: str, mid: uuid.UUID) -> Config:
    sym = f"mkt-{mid}"
    cfg = Config(db_path=":memory:")
    object.__setattr__(
        cfg,
        "_raw_settings",
        {
            "risk": {
                "per_users": [
                    {
                        "user_id": user_id,
                        "engaged": False,
                        "max_gross_exposure": 1.0,
                        "max_position_size": 0.5,
                        "max_positions_total": 10,
                        "allowed_markets": [sym],
                    }
                ]
            }
        },
    )
    return cfg


def test_replay_endpoint_reflects_real_orders() -> None:
    server._orch_cache.clear()
    try:
        user_id = str(uuid.uuid4())
        mid = uuid.uuid4()
        sym = f"mkt-{mid}"
        allowed_mid = uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{sym}")
        cfg = _profile_config(user_id, mid)
        orch = build_orchestrator(mode="paper", config=cfg)

        result = orch.submit_retail_order(allowed_mid, "buy", 5.0, 100.0, user_id=user_id)
        assert result.allowed, result.reason

        # Point the server at our orchestrator so the endpoint reads the same state.
        server._orch_cache["paper"] = orch
        client = TestClient(server.build_app())

        start = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        end = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        resp = client.get(f"/v1/attribution/replay?start={quote(start)}&end={quote(end)}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "paper"
        # The retail order produced a complete causal chain (decision -> order -> fill).
        assert body["total_realized_pnl"] == 0.0
        chains = [c for c in body["chains"] if c["complete"]]
        assert len(chains) >= 1
    finally:
        server._orch_cache.clear()


def test_replay_endpoint_rejects_inverted_window() -> None:
    server._orch_cache.clear()
    try:
        client = TestClient(server.build_app())
        later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        earlier = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        resp = client.get(f"/v1/attribution/replay?start={quote(later)}&end={quote(earlier)}")
        assert resp.status_code == 422
    finally:
        server._orch_cache.clear()


def test_fill_dict_none_returns_none() -> None:
    from traderos.interfaces.api.attribution import _fill_dict

    assert _fill_dict(None) is None
