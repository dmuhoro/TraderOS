"""Sprint 1: asyncio-native ParetoWebSocketIngestor.

Offline tests: the transport is injected, so the reconnect loop, bounded-queue
drop behavior, malformed-tick accounting, cancellation safety and the
pipeline consumer are all proven without any network access (Constitution §2
Principle 6 — Test Before Trust).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from traderos.infrastructure.async_streaming import AsyncBinanceStreamTransport
from traderos.infrastructure.async_streaming import AsyncWebSocket
from traderos.infrastructure.async_streaming import ParetoWebSocketIngestor
from traderos.infrastructure.async_streaming import _default_connector
from traderos.infrastructure.async_streaming import parse_frame_text
from traderos.infrastructure.market_stream import InvalidTickError


def _raw(symbol: str = "BTCUSDT", price: object = 30000.5, quantity: object = 1.0) -> dict:
    return {
        "symbol": symbol,
        "price": price,
        "quantity": quantity,
        "timestamp": time.time(),
        "source": "binance",
    }


class _FakeWS:
    def __init__(self, frames: list[object], *, close_raises: bool = False) -> None:
        self._frames = iter(frames)
        self.closed = False
        self.sent: list[str] = []
        self._close_raises = close_raises
        self._block = asyncio.Event()

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> object | None:
        if self._block.is_set():
            await self._block.wait()
        return next(self._frames, None)

    async def close(self) -> None:
        self.closed = True
        if self._close_raises:
            raise RuntimeError("close failed")


class _FakeTransport:
    """Each element of ``script`` is a list of frames or an Exception."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.connects = 0
        self.closed = False
        self.last_symbols: list[str] = []

    async def connect(self, symbols: list[str]) -> AsyncWebSocket:
        self.connects += 1
        self.last_symbols = list(symbols)
        item = self._script.pop(0) if self._script else []
        if isinstance(item, BaseException):
            raise item
        return _FakeWS(list(item))

    async def close(self) -> None:
        self.closed = True

    def parse_frame(self, frame: str) -> dict | None:
        if frame == "skip":
            return None
        if frame == "raise":
            raise ValueError("boom")
        return json.loads(frame)


def _run(coro):
    return asyncio.run(coro)


async def _ingest_and_wait(ingestor: ParetoWebSocketIngestor, frames: list[object], n: int) -> None:
    task = asyncio.create_task(ingestor.run())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while ingestor.pending_count() < n and loop.time() < deadline:
        await asyncio.sleep(0.005)
    await ingestor.stop()
    await task


def test_ingests_valid_ticks_and_drains() -> None:
    frames = [json.dumps(_raw()), json.dumps(_raw(price=30001.0))]
    ingestor = ParetoWebSocketIngestor(_FakeTransport([frames]), max_reconnects=0)

    async def scenario() -> list:
        seen: list = []
        await _ingest_and_wait(ingestor, frames, 2)
        await ingestor.drain(seen.append)
        return seen

    seen = _run(scenario())
    assert len(seen) == 2
    assert seen[0].symbol == "BTCUSDT"
    assert seen[0].price == 30000.5
    assert seen[1].price == 30001.0
    assert ingestor.messages_received == 2
    assert ingestor.malformed_ticks == 0
    assert ingestor.dropped_ticks == 0


def test_reconnects_with_exponential_backoff_after_drop() -> None:
    frames = [json.dumps(_raw())]
    transport = _FakeTransport([frames, [json.dumps(_raw(price=31000.0))], []])
    ingestor = ParetoWebSocketIngestor(
        transport,
        base_backoff=0.001,
        max_backoff=0.05,
        backoff_factor=2.0,
        jitter=0.0,
    )

    async def scenario() -> list:
        seen: list = []
        task = asyncio.create_task(ingestor.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while len(seen) < 2 and loop.time() < deadline:
            await ingestor.drain(seen.append)
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task
        return seen

    seen = _run(scenario())
    assert len(seen) == 2
    assert transport.connects >= 2
    assert ingestor.reconnects >= 1
    assert ingestor.last_backoff_delay > 0.0
    assert transport.last_symbols == []


def test_max_reconnects_stops_ingestor() -> None:
    transport = _FakeTransport([RuntimeError("down")])
    ingestor = ParetoWebSocketIngestor(
        transport,
        base_backoff=0.001,
        max_backoff=0.05,
        jitter=0.0,
        max_reconnects=2,
    )
    _run(ingestor.run())
    assert ingestor.running is False
    assert ingestor.reconnects == 3
    assert ingestor.connected is False


def test_queue_full_drops_never_blocks() -> None:
    frames = [json.dumps(_raw()), json.dumps(_raw(price=32000.0))]
    ingestor = ParetoWebSocketIngestor(_FakeTransport([frames]), max_queue=1, max_reconnects=0)

    async def scenario() -> None:
        task = asyncio.create_task(ingestor.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while ingestor.messages_received < 2 and loop.time() < deadline:
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task

    _run(scenario())
    assert ingestor.pending_count() == 1
    assert ingestor.dropped_ticks == 1


def test_malformed_and_skipped_frames_accounted() -> None:
    frames = ["skip", json.dumps(_raw(price="not-a-number")), json.dumps(_raw())]
    ingestor = ParetoWebSocketIngestor(_FakeTransport([frames]), max_reconnects=0)

    async def scenario() -> None:
        await _ingest_and_wait(ingestor, frames, 1)

    _run(scenario())
    assert ingestor.messages_received == 1
    assert ingestor.malformed_ticks == 1


def test_parse_error_treated_as_transport_outage() -> None:
    transport = _FakeTransport([["raise"], [json.dumps(_raw())], []])
    ingestor = ParetoWebSocketIngestor(transport, base_backoff=0.001, max_backoff=0.05, jitter=0.0)

    async def scenario() -> list:
        seen: list = []
        task = asyncio.create_task(ingestor.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while len(seen) < 1 and loop.time() < deadline:
            await ingestor.drain(seen.append)
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task
        return seen

    seen = _run(scenario())
    assert len(seen) == 1
    assert ingestor.reconnects >= 1


def test_stop_is_responsive_during_backoff() -> None:
    transport = _FakeTransport([RuntimeError("down")])
    ingestor = ParetoWebSocketIngestor(transport, base_backoff=30.0, max_backoff=30.0, jitter=0.0)

    async def scenario() -> float:
        task = asyncio.create_task(ingestor.run())
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await ingestor.stop()
        await task
        return time.monotonic() - started

    elapsed = _run(scenario())
    assert elapsed < 5.0


def test_cancellation_propagates() -> None:
    ingestor = ParetoWebSocketIngestor(
        _FakeTransport([RuntimeError("down")]), base_backoff=5.0, max_backoff=5.0
    )

    async def scenario() -> None:
        task = asyncio.create_task(ingestor.run())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario())


def test_run_pipeline_dispatches_until_stopped() -> None:
    frames = [json.dumps(_raw()), json.dumps(_raw(price=33000.0))]
    ingestor = ParetoWebSocketIngestor(
        _FakeTransport([frames, []]), base_backoff=0.001, max_backoff=0.05, jitter=0.0
    )
    seen: list = []

    async def scenario() -> None:
        task = asyncio.create_task(ingestor.run_pipeline(seen.append, poll_interval=0.005))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while len(seen) < 2 and loop.time() < deadline:
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task

    _run(scenario())
    assert len(seen) == 2
    assert ingestor.running is False


def test_health_lifecycle() -> None:
    ingestor = ParetoWebSocketIngestor(_FakeTransport([]))

    async def scenario() -> None:
        await ingestor.start()
        fresh = ingestor.health()
        assert fresh.healthy is True
        ingestor._last_tick = datetime.now(UTC) - timedelta(seconds=400)
        stale = ingestor.health()
        assert stale.healthy is False
        assert stale.service == "market-data-async"
        await ingestor.stop()

    _run(scenario())


def test_subscribe_and_status() -> None:
    ingestor = ParetoWebSocketIngestor(_FakeTransport([]), max_queue=4)
    ingestor.subscribe(["ETHUSDT"])
    status = ingestor.status()
    assert status["symbols"] == ["ETHUSDT"]
    assert status["running"] is False
    assert "pending" in status and "dropped" in status


def test_async_binance_transport_parses_and_closes() -> None:
    async def scenario() -> None:
        transport = AsyncBinanceStreamTransport(source="binance")
        frame = json.dumps(
            {
                "stream": "btcusdt@aggTrade",
                "data": {
                    "e": "aggTrade",
                    "s": "BTCUSDT",
                    "p": "30000.5",
                    "q": "1.0",
                    "T": int(time.time() * 1000),
                    "a": 99,
                },
            }
        )
        parsed = transport.parse_frame(frame)
        assert parsed is not None
        assert parsed["symbol"] == "BTCUSDT"
        assert transport.parse_frame(json.dumps({"e": "kline", "s": "BTCUSDT"})) is None
        assert transport.parse_frame("not json") is None
        await transport.close()
        await transport.close()

    _run(scenario())


def test_default_connector_import_error() -> None:
    async def scenario() -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("websockets"):
                raise ImportError("websockets missing")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with pytest.raises(RuntimeError, match="websockets"):
                await _default_connector("wss://example.invalid/stream")
        finally:
            builtins.__import__ = real_import

    _run(scenario())


def test_parse_frame_text_bytes() -> None:
    assert parse_frame_text(b"hello") == "hello"
    assert parse_frame_text("hello") == "hello"


def test_validate_rejects_invalid_tick_surfaces() -> None:
    from traderos.infrastructure.market_stream import validate_tick

    with pytest.raises(InvalidTickError):
        validate_tick({"symbol": "X", "price": "nope", "quantity": "1", "timestamp": time.time()})


def test_default_connector_success_path() -> None:
    async def scenario() -> None:
        import websockets.asyncio.client as ws_client

        async def fake_connect(url: str) -> _FakeWS:
            assert url.startswith("wss://example.invalid/stream")
            return _FakeWS([])

        original = ws_client.connect
        ws_client.connect = fake_connect
        try:
            ws = await _default_connector("wss://example.invalid/stream")
            assert isinstance(ws, _FakeWS)
        finally:
            ws_client.connect = original

    _run(scenario())


def test_binance_transport_connect_sends_subscription() -> None:
    async def scenario() -> None:
        ws = _FakeWS([])
        connector_calls = []

        async def connector(url: str) -> AsyncWebSocket:
            connector_calls.append(url)
            return ws

        transport = AsyncBinanceStreamTransport(url="wss://example.invalid", connector=connector)
        opened = await transport.connect(["BTCUSDT"])
        assert opened is ws
        assert ws.sent == ['{"method": "SUBSCRIBE", "params": ["btcusdt@aggTrade"], "id": 1}']
        await transport.close()
        assert ws.closed is True
        assert connector_calls[0].startswith("wss://example.invalid/stream")

    _run(scenario())


def test_binance_transport_close_swallows() -> None:
    async def scenario() -> None:
        ws = _FakeWS([], close_raises=True)

        async def connector(url: str) -> AsyncWebSocket:
            return ws

        transport = AsyncBinanceStreamTransport(url="wss://example.invalid", connector=connector)
        await transport.connect(["BTCUSDT"])
        await transport.close()
        assert transport._ws is None

    _run(scenario())


def test_put_prunes_latency_history() -> None:
    frames = [json.dumps(_raw(price=float(i + 1))) for i in range(2001)]
    ingestor = ParetoWebSocketIngestor(_FakeTransport([frames]), max_reconnects=0)

    async def scenario() -> None:
        task = asyncio.create_task(ingestor.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30.0
        while ingestor.messages_received < 2001 and loop.time() < deadline:
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task

    _run(scenario())
    assert ingestor.messages_received == 2001
    assert len(ingestor._latencies) == 1001


def test_cancelled_connect_propagates() -> None:
    transport = _FakeTransport([asyncio.CancelledError("stopped")])
    ingestor = ParetoWebSocketIngestor(transport)
    with pytest.raises(asyncio.CancelledError):
        _run(ingestor.run())


def test_cancelled_recv_propagates() -> None:
    class _CancelWS:
        async def send(self, payload: str) -> None:
            pass

        async def recv(self) -> object:
            raise asyncio.CancelledError("stopped")

        async def close(self) -> None:
            pass

    class _CancelTransport:
        async def connect(self, symbols: list[str]) -> AsyncWebSocket:
            return _CancelWS()

        async def close(self) -> None:
            pass

        def parse_frame(self, frame: str) -> dict | None:
            return json.loads(frame)

    ingestor = ParetoWebSocketIngestor(_CancelTransport())
    with pytest.raises(asyncio.CancelledError):
        _run(ingestor.run())


def test_teardown_close_error_swallowed() -> None:
    class _CloseRaiseWS(_FakeWS):
        async def close(self) -> None:
            raise RuntimeError("close boom")

    transport = _FakeTransport([])

    class _T:
        async def connect(self, symbols: list[str]) -> AsyncWebSocket:
            transport.connects += 1
            transport.last_symbols = list(symbols)
            return _CloseRaiseWS([json.dumps(_raw())])

        async def close(self) -> None:
            transport.closed = True

        def parse_frame(self, frame: str) -> dict | None:
            return json.loads(frame)

    ingestor = ParetoWebSocketIngestor(
        _T(), base_backoff=0.001, max_backoff=0.05, jitter=0.0, max_reconnects=2
    )

    async def scenario() -> list:
        seen: list = []
        task = asyncio.create_task(ingestor.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while len(seen) < 1 and loop.time() < deadline:
            await ingestor.drain(seen.append)
            await asyncio.sleep(0.005)
        await ingestor.stop()
        await task
        return seen

    assert len(_run(scenario())) >= 1


def test_stop_breaks_blocked_receive_loop() -> None:
    class _BlockWS:
        def __init__(self) -> None:
            self._release = asyncio.Event()

        async def send(self, payload: str) -> None:
            pass

        async def recv(self) -> object:
            await self._release.wait()
            return None

        async def close(self) -> None:
            self._release.set()

    class _T:
        def __init__(self) -> None:
            self._ws: _BlockWS | None = None

        async def connect(self, symbols: list[str]) -> AsyncWebSocket:
            self._ws = _BlockWS()
            return self._ws

        async def close(self) -> None:
            if self._ws is not None:
                await self._ws.close()

        def parse_frame(self, frame: str) -> dict | None:
            return json.loads(frame)

    ingestor = ParetoWebSocketIngestor(_T())

    async def scenario() -> float:
        task = asyncio.create_task(ingestor.run())
        await asyncio.sleep(0.02)
        started = time.monotonic()
        await ingestor.stop()
        await task
        return time.monotonic() - started

    elapsed = _run(scenario())
    assert elapsed < 5.0
    assert ingestor.running is False


def test_drain_max_items() -> None:
    frames = [json.dumps(_raw()), json.dumps(_raw(price=34000.0))]
    ingestor = ParetoWebSocketIngestor(_FakeTransport([frames]), max_reconnects=0)

    async def scenario() -> tuple:
        await _ingest_and_wait(ingestor, frames, 2)
        first: list = []
        n1 = await ingestor.drain(first.append, max_items=1)
        rest: list = []
        n2 = await ingestor.drain(rest.append)
        return n1, n2, len(first), len(rest)

    n1, n2, f, r = _run(scenario())
    assert (n1, n2, f, r) == (1, 1, 1, 1)
