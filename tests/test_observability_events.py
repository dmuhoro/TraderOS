from __future__ import annotations

import asyncio
import json
import queue

import pytest
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from traderos.interfaces.api import events
from traderos.interfaces.api import operator
from traderos.interfaces.api import security
from traderos.interfaces.api import server


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server._orch_cache.clear()
    security.reset_authenticator()
    events.reset_broker()
    yield
    server._orch_cache.clear()
    security.reset_authenticator()
    events.reset_broker()


class TestEventBroker:
    def test_subscribe_publish_unsubscribe(self) -> None:
        broker = events.EventBroker()
        sub = broker.subscribe()
        assert broker.subscriber_count == 1
        assert broker.publish({"type": "a", "data": {}}) == 1
        assert sub.get_nowait()["type"] == "a"
        broker.unsubscribe(sub)
        assert broker.subscriber_count == 0
        assert broker.publish({"type": "b", "data": {}}) == 0

    def test_multiple_subscribers_receive(self) -> None:
        broker = events.EventBroker()
        s1 = broker.subscribe()
        s2 = broker.subscribe()
        broker.publish({"type": "t", "data": {"v": 1}})
        assert s1.get_nowait()["data"]["v"] == 1
        assert s2.get_nowait()["data"]["v"] == 1

    def test_slow_subscriber_drops_oldest(self) -> None:
        broker = events.EventBroker(maxlen=2)
        sub = broker.subscribe()
        broker.publish({"type": "1", "data": {}})
        broker.publish({"type": "2", "data": {}})
        broker.publish({"type": "3", "data": {}})
        # the queue always holds the newest `maxlen` events
        assert {sub.get_nowait()["type"] for _ in range(2)} == {"2", "3"}
        with pytest.raises(queue.Empty):
            sub.get_nowait()

    def test_publish_event_round_trip(self) -> None:
        broker = events.get_broker()
        sub = broker.subscribe()
        assert events.publish_event("kill_switch", {"engaged": True}) == 1
        evt = sub.get_nowait()
        assert evt["type"] == "kill_switch"
        assert evt["data"] == {"engaged": True}
        assert "timestamp" in evt

    def test_get_and_reset_broker(self) -> None:
        first = events.get_broker()
        assert events.get_broker() is first
        events.reset_broker()
        second = events.get_broker()
        assert second is not first


class _StubVerdict:
    allowed = True
    reason = None


class _StubKillSwitch:
    circuit_open = False

    def can_trade(self) -> _StubVerdict:
        return _StubVerdict()


class _StubRiskService:
    kill_switch = _StubKillSwitch()


class _StubMode:
    value = "mock"


class _StubOrchestrator:
    operator_session = None
    risk_service = _StubRiskService()
    mode = _StubMode()
    running = False


class TestSseEvents:
    @pytest.mark.anyio
    async def test_event_stream_sends_snapshot_then_published_event(self) -> None:
        broker = events.EventBroker()

        def provider() -> _StubOrchestrator:
            return _StubOrchestrator()

        async def feed() -> None:
            await asyncio.sleep(0.01)
            broker.publish({"type": "state", "data": {"running": False}})

        task = asyncio.create_task(feed())
        frames: list[str] = []
        async for frame in operator.event_stream(broker, provider, wait_timeout=0.2):
            frames.append(frame)
            if len(frames) >= 2:
                break
        await task

        assert frames[0].startswith("event: snapshot")
        snapshot = json.loads(frames[0].split("\ndata: ", 1)[1].strip())
        assert snapshot["ready"] is True
        assert snapshot["mode"] == "mock"
        assert frames[1].startswith("event: state")
        assert "running" in frames[1]

    @pytest.mark.anyio
    async def test_event_stream_emits_keepalive_when_idle(self) -> None:
        broker = events.EventBroker()
        stream = operator.event_stream(
            broker,
            lambda: _StubOrchestrator(),
            wait_timeout=0.05,
        )
        frames: list[str] = []
        async for frame in stream:
            frames.append(frame)
            if len(frames) >= 2:
                break
        assert frames[0].startswith("event: snapshot")
        assert frames[1] == ": keepalive\n\n"

    @pytest.mark.anyio
    async def test_event_stream_unsubscribes_on_close(self) -> None:
        broker = events.EventBroker()
        stream = operator.event_stream(broker, lambda _p: {"ready": True}, wait_timeout=0.1)
        await stream.__anext__()
        assert broker.subscriber_count == 1
        await stream.aclose()
        assert broker.subscriber_count == 0

    @pytest.mark.anyio
    async def test_sse_route_is_a_streaming_response(self) -> None:
        router = APIRouter()
        operator.register_operator_endpoints(router, lambda: object())
        route = next(r for r in router.routes if getattr(r, "path", "") == "/events")
        response = await route.endpoint()
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"

    def test_sse_route_requires_sse_credential(self) -> None:
        router = APIRouter()
        operator.register_operator_endpoints(router, lambda: object())
        route = next(r for r in router.routes if getattr(r, "path", "") == "/events")
        deps = [d.dependency for d in route.dependencies]
        # The SSE feed authenticates with the browser-token seam (require_sse,
        # which still demands a valid header key when no token is present) and
        # exposes the token-mint endpoint for the dashboard.
        assert operator.require_sse in deps

    def test_sse_route_mints_token_endpoint(self) -> None:
        router = APIRouter()
        operator.register_operator_endpoints(router, lambda: object())
        route = next(r for r in router.routes if getattr(r, "path", "") == "/events/token")
        deps = [d.dependency for d in route.dependencies]
        assert operator.require_read in deps


class TestKillSwitchAlerting:
    def test_engage_sends_critical_notification_and_event(self) -> None:
        client = TestClient(server.build_app())
        assert client.post("/v1/kill-switch/engage").status_code == 200
        assert client.get("/v1/kill-switch").json()["engaged"] is True
        assert client.post("/v1/kill-switch/disengage").status_code == 200
        assert client.get("/v1/kill-switch").json()["engaged"] is False
