"""Asyncio-native market-data streaming ingestor (Sprint 1 / Pareto core).

The existing ``StreamingMarketDataService`` (``market_stream.py``) drives a
*synchronous* ``websockets`` transport inside a worker thread. This module adds
the asyncio-native path the hardened engine needs:

- ``ParetoWebSocketIngestor``: an ``asyncio`` task that owns the WebSocket
  lifecycle, feeds validated immutable ``Tick`` objects into a bounded
  ``asyncio.Queue`` and reconnects with exponential backoff + jitter when the
  socket drops. Enqueueing is ``put_nowait`` with a drop counter — the reader
  loop never blocks on a slow consumer, so a flood of ticks cannot stall the
  ingest task.
- ``AsyncBinanceStreamTransport``: a provider transport implementing the
  ``AsyncStreamTransport`` protocol. Frame parsing and tick validation reuse the
  pure, unit-tested helpers in ``market_stream.py`` (``parse_trade_frame``,
  ``validate_tick``, ``build_stream_url``, ``build_subscription_frame``); only
  the transport is new. The ``websockets`` asyncio client is imported lazily so
  the module loads and is fully testable without the package installed.

Domain separation is preserved: this is pure infrastructure. It never imports
domain services and exposes ticks through the existing ``Tick`` value the
strategy pipeline already consumes.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Protocol
from typing import cast

from traderos.domain.ports import HealthStatus
from traderos.infrastructure.market_stream import ClockMonitor
from traderos.infrastructure.market_stream import InvalidTickError
from traderos.infrastructure.market_stream import ReplayRecorder
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.market_stream import build_stream_url
from traderos.infrastructure.market_stream import build_subscription_frame
from traderos.infrastructure.market_stream import parse_trade_frame
from traderos.infrastructure.market_stream import validate_tick

_HEARTBEAT_TIMEOUT = 30.0


def _now() -> datetime:
    return datetime.now(UTC)


class AsyncWebSocket(Protocol):
    """Minimal async WebSocket surface the ingestor relies on.

    ``recv()`` returns ``None`` on a clean close; ``send`` is used for the
    subscription frame; ``close`` must be cancellation-safe and idempotent.
    """

    async def send(self, payload: str) -> None: ...

    async def recv(self) -> str | bytes | None: ...

    async def close(self) -> None: ...


class AsyncStreamTransport(Protocol):
    """Transport seam so provider SDKs stay infrastructure-only (ADR-006)."""

    async def connect(self, symbols: list[str]) -> AsyncWebSocket: ...

    async def close(self) -> None: ...

    def parse_frame(self, frame: str) -> dict[str, Any] | None: ...


async def _default_connector(url: str) -> AsyncWebSocket:
    """Open an asyncio WebSocket to ``url`` (lazy ``websockets`` import)."""
    try:
        from websockets.asyncio.client import connect as ws_connect
    except ImportError as exc:
        raise RuntimeError(
            "Live async streaming requires the 'websockets' package: " "pip install 'traderos[all]'"
        ) from exc
    # websockets' ClientConnection satisfies the AsyncWebSocket surface
    # (send str payload / recv str|bytes / close); its send() accepts a
    # superset of str, so narrow it explicitly at this adapter boundary.
    return cast(AsyncWebSocket, await ws_connect(url))


class AsyncBinanceStreamTransport:
    """Asyncio Binance aggregate-trade transport (Sprint 1 / OT-001)."""

    def __init__(
        self,
        *,
        url: str | None = None,
        connector: Callable[[str], Awaitable[AsyncWebSocket]] | None = None,
        source: str = "binance",
    ) -> None:
        self._url = url or "wss://stream.binance.com:9443"
        self._connector = connector or _default_connector
        self._source = source
        self._ws: AsyncWebSocket | None = None

    async def connect(self, symbols: list[str]) -> AsyncWebSocket:
        await self.close()
        ws = await self._connector(build_stream_url(symbols, base=self._url))
        self._ws = ws
        await ws.send(build_subscription_frame(symbols))
        return ws

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: S110, BLE001 — best-effort teardown
                pass

    def parse_frame(self, frame: str) -> dict[str, Any] | None:
        return parse_trade_frame(frame, source=self._source)


class ParetoWebSocketIngestor:
    """Non-blocking asyncio market-data ingestor with robust reconnect.

    Owns a single asyncio task per ``run()``; validated ticks land in a bounded
    ``asyncio.Queue`` consumed by the strategy pipeline via ``drain()`` or
    ``run_pipeline()``. Socket drops are absorbed by an exponential-backoff
    reconnect loop (base * factor**attempt capped at ``max_backoff``, with
    optional jitter) that never kills the process — the application keeps
    running while the feed recovers.
    """

    def __init__(
        self,
        transport: AsyncStreamTransport,
        symbols: list[str] | None = None,
        *,
        max_queue: int = 10_000,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: float = 0.1,
        max_reconnects: int | None = None,
        max_staleness_seconds: float = 300.0,
        max_future_seconds: float = 60.0,
        heartbeat_timeout: float = _HEARTBEAT_TIMEOUT,
        clock: ClockMonitor | None = None,
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self._transport = transport
        self._symbols: list[str] = list(symbols or [])
        self.max_queue = max_queue
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self.max_reconnects = max_reconnects
        self.max_staleness_seconds = max_staleness_seconds
        self.max_future_seconds = max_future_seconds
        self.heartbeat_timeout = heartbeat_timeout
        self.clock = clock or ClockMonitor()
        self.recorder = recorder or ReplayRecorder()
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=max_queue)
        self._stop_event = asyncio.Event()
        self._running = False
        self._connected = False
        self._last_tick: datetime | None = None
        self._latencies: list[float] = []
        self._dropped = 0
        self._malformed = 0
        self._reconnects = 0
        self._messages = 0
        self._last_backoff_delay = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        return self._connected

    def subscribe(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)

    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_ticks(self) -> int:
        return self._dropped

    @property
    def malformed_ticks(self) -> int:
        return self._malformed

    @property
    def reconnects(self) -> int:
        return self._reconnects

    @property
    def messages_received(self) -> int:
        return self._messages

    @property
    def last_backoff_delay(self) -> float:
        return self._last_backoff_delay

    async def start(self) -> None:
        self._stop_event.clear()
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        self._stop_event.set()
        await self._transport.close()

    async def _put(self, raw: dict[str, Any]) -> None:
        received = _now()
        validated = validate_tick(
            raw,
            now=received,
            max_staleness_seconds=self.max_staleness_seconds,
            max_future_seconds=self.max_future_seconds,
        )
        tick = Tick(
            validated["symbol"],
            validated["price"],
            validated["quantity"],
            validated["exchange_timestamp"],
            received,
            validated["source"],
            validated["event_id"],
        )
        self.clock.observe(tick.exchange_timestamp, received)
        self.recorder.record(tick)
        self._last_tick = received
        self._latencies.append(tick.latency_ms)
        if len(self._latencies) > 2000:
            del self._latencies[:1000]
        try:
            self._queue.put_nowait(tick)
        except asyncio.QueueFull:
            self._dropped += 1

    async def _backoff(self, attempts: int) -> bool:
        """Wait for the next reconnect attempt, responsive to ``stop()``.

        Returns ``False`` when the ingestor must give up (stopped, or
        ``max_reconnects`` total reconnect attempts reached). ``attempts``
        counts consecutive *connect* failures and drives the exponential
        growth; ``max_reconnects`` bounds the total number of backoff waits.
        """
        if self.max_reconnects is not None and self._reconnects > self.max_reconnects:
            return False
        delay = min(self.base_backoff * (self.backoff_factor ** max(attempts, 0)), self.max_backoff)
        if self.jitter > 0:
            delay += random.uniform(0, self.jitter)
        self._last_backoff_delay = delay
        self._reconnects += 1
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass
        return self._running

    async def run(self) -> None:
        """Ingest frames until stopped, reconnecting with exponential backoff."""
        await self.start()
        try:
            await self._run_loop()
        finally:
            self._running = False

    async def _run_loop(self) -> None:
        attempts = 0
        while self._running:
            ws: AsyncWebSocket | None = None
            try:
                ws = await self._transport.connect(list(self._symbols))
                attempts = 0
                self._connected = True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — any transport failure must be absorbed by reconnect
                self._connected = False
                if not await self._backoff(attempts):
                    break
                attempts += 1
                continue
            try:
                while self._running:
                    frame = await ws.recv()
                    if frame is None:
                        break
                    text = (
                        frame.decode("utf-8", errors="replace")
                        if isinstance(frame, bytes)
                        else frame
                    )
                    parsed = self._transport.parse_frame(text)
                    if parsed is None:
                        continue
                    try:
                        await self._put(parsed)
                        self._messages += 1
                    except InvalidTickError:
                        self._malformed += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: S110, BLE001 — transport error; reconnect
                pass
            finally:
                self._connected = False
                try:
                    await ws.close()
                except Exception:  # noqa: S110, BLE001 — best-effort teardown
                    pass
            if not self._running:
                break
            attempts += 1
            if not await self._backoff(attempts):
                break

    async def drain(self, handler: Callable[[Tick], Any], max_items: int | None = None) -> int:
        """Pop queued ticks and hand them to ``handler`` (never blocks)."""
        count = 0
        while not self._queue.empty() and (max_items is None or count < max_items):
            handler(self._queue.get_nowait())
            count += 1
        return count

    async def run_pipeline(
        self, handler: Callable[[Tick], Any], *, poll_interval: float = 0.01
    ) -> None:
        """Run ingestion and dispatch to ``handler`` until stopped.

        Cancellation-safe: stops the ingestor on exit so a cancelled task never
        leaves a live socket behind.
        """
        try:
            await self.start()
            async with asyncio.TaskGroup() as group:
                group.create_task(self.run())
                while self._running:
                    await self.drain(handler)
                    await asyncio.sleep(poll_interval)
        finally:
            await self.stop()

    def health(self) -> HealthStatus:
        now = _now()
        alive = self._running and (
            self._last_tick is None
            or (now - self._last_tick).total_seconds() <= self.heartbeat_timeout
        )
        latency = (
            sum(self._latencies[-100:]) / len(self._latencies[-100:]) if self._latencies else 0.0
        )
        return HealthStatus(
            "market-data-async", alive, "ok" if alive else "heartbeat timeout", latency, now
        )

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "connected": self._connected,
            "symbols": list(self._symbols),
            "pending": self._queue.qsize(),
            "dropped": self._dropped,
            "malformed": self._malformed,
            "reconnects": self._reconnects,
            "messages": self._messages,
            "last_backoff_delay": self._last_backoff_delay,
        }


def parse_frame_text(frame: str | bytes) -> str:
    """Normalize a raw frame to text (protocol helper for transports)."""
    if isinstance(frame, bytes):
        return frame.decode("utf-8", errors="replace")
    return frame
