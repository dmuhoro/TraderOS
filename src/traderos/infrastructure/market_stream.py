"""Provider-neutral streaming market data infrastructure.

The transport is injected so the Binance implementation can be tested without
network access and future providers can share the same pipeline.
"""

from __future__ import annotations

import json
import queue
import time
import uuid
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any
from typing import Protocol

from traderos.domain.ports import HealthStatus
from traderos.domain.ports import MarketDataPort


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: Decimal
    quantity: Decimal
    exchange_timestamp: datetime
    received_timestamp: datetime
    source: str = "binance"
    event_id: str = ""

    @property
    def latency_ms(self) -> float:
        return max(0.0, (self.received_timestamp - self.exchange_timestamp).total_seconds() * 1000)


@dataclass(frozen=True)
class Candle:
    symbol: str
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    ticks: int


class StreamTransport(Protocol):
    def connect(self, symbols: list[str]) -> Iterable[dict[str, Any]]: ...
    def close(self) -> None: ...


class ClockMonitor:
    def __init__(self, max_drift_ms: float = 1000.0) -> None:
        self.max_drift_ms = max_drift_ms
        self.last_drift_ms = 0.0

    def observe(self, exchange_time: datetime, now: datetime | None = None) -> bool:
        current = now or datetime.now(tz=UTC)
        self.last_drift_ms = (current - exchange_time).total_seconds() * 1000
        return abs(self.last_drift_ms) <= self.max_drift_ms


class CandleAggregator:
    def __init__(self, interval_seconds: int = 60) -> None:
        self.interval = interval_seconds
        self._current: dict[str, list[Any]] = {}

    def add(self, tick: Tick) -> Candle | None:
        epoch = int(tick.exchange_timestamp.timestamp())
        start = datetime.fromtimestamp(epoch - epoch % self.interval, tz=UTC)
        bucket = self._current.get(tick.symbol)
        if bucket and bucket[0] != start:
            candle = self._make(bucket)
            self._current[tick.symbol] = [start, tick]
            return candle
        if not bucket:
            self._current[tick.symbol] = [start, tick]
        else:
            bucket.append(tick)
        return None

    def _make(self, bucket: list[Any]) -> Candle:
        start, *ticks = bucket
        return Candle(
            ticks[0].symbol,
            start,
            start + timedelta(seconds=self.interval),
            ticks[0].price,
            max(t.price for t in ticks),
            min(t.price for t in ticks),
            ticks[-1].price,
            sum((t.quantity for t in ticks), Decimal(0)),
            len(ticks),
        )


class ReplayRecorder:
    def __init__(self) -> None:
        self.records: list[str] = []

    def record(self, tick: Tick) -> None:
        self.records.append(
            json.dumps(
                {
                    "event_id": tick.event_id,
                    "symbol": tick.symbol,
                    "price": str(tick.price),
                    "quantity": str(tick.quantity),
                    "exchange_timestamp": tick.exchange_timestamp.isoformat(),
                    "received_timestamp": tick.received_timestamp.isoformat(),
                    "source": tick.source,
                },
                sort_keys=True,
            )
        )

    def replay(self) -> list[Tick]:
        return [self._decode(line) for line in self.records]

    @staticmethod
    def _decode(line: str) -> Tick:
        value = json.loads(line)
        return Tick(
            value["symbol"],
            Decimal(value["price"]),
            Decimal(value["quantity"]),
            datetime.fromisoformat(value["exchange_timestamp"]),
            datetime.fromisoformat(value["received_timestamp"]),
            value["source"],
            value["event_id"],
        )


class StreamingMarketDataService(MarketDataPort):
    def __init__(
        self,
        transport: StreamTransport,
        *,
        max_queue: int = 10_000,
        reconnect_limit: int = 3,
        heartbeat_timeout: float = 30.0,
        clock: ClockMonitor | None = None,
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self.transport, self.max_queue, self.reconnect_limit, self.heartbeat_timeout = (
            transport,
            max_queue,
            reconnect_limit,
            heartbeat_timeout,
        )
        self.clock, self.recorder = clock or ClockMonitor(), recorder or ReplayRecorder()
        self._queue: queue.Queue[Tick] = queue.Queue(maxsize=max_queue)
        self._handler: Callable[[Tick], None] | None = None
        self._symbols: list[str] = []
        self._running = False
        self._last_tick: datetime | None = None
        self._dropped = 0
        self._latencies: list[float] = []

    def subscribe(self, symbols: list[str], handler: Callable[[Tick], None]) -> None:
        self._symbols, self._handler = symbols, handler

    def ingest(self, raw: dict[str, Any]) -> Tick:
        received = datetime.now(tz=UTC)
        exchange = datetime.fromtimestamp(float(raw.get("timestamp", received.timestamp())), tz=UTC)
        tick = Tick(
            str(raw["symbol"]),
            Decimal(str(raw["price"])),
            Decimal(str(raw.get("quantity", "0"))),
            exchange,
            received,
            str(raw.get("source", "binance")),
            str(raw.get("event_id", uuid.uuid4())),
        )
        self.clock.observe(exchange, received)
        self.recorder.record(tick)
        self._last_tick = received
        self._latencies.append(tick.latency_ms)
        try:
            self._queue.put_nowait(tick)
        except queue.Full:
            self._dropped += 1
        return tick

    def drain(self, limit: int | None = None) -> int:
        count = 0
        while not self._queue.empty() and (limit is None or count < limit):
            tick = self._queue.get_nowait()
            if self._handler:
                self._handler(tick)
            count += 1
        return count

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        self.transport.close()

    def run(self, *, max_messages: int | None = None) -> int:
        """Consume a transport stream, reconnecting with bounded attempts."""
        self.start()
        received = 0
        attempts = 0
        while self._running and (max_messages is None or received < max_messages):
            try:
                for raw in self.transport.connect(self._symbols):
                    if not self._running:
                        break
                    self.ingest(raw)
                    self.drain()
                    received += 1
                    if max_messages is not None and received >= max_messages:
                        break
                attempts = 0
            except Exception:
                attempts += 1
                if attempts > self.reconnect_limit:
                    self._running = False
                    break
                time.sleep(min(2 ** (attempts - 1), 30))
        return received

    def health(self) -> HealthStatus:
        now = datetime.now(tz=UTC)
        alive = self._running and (
            self._last_tick is None
            or (now - self._last_tick).total_seconds() <= self.heartbeat_timeout
        )
        latency = (
            sum(self._latencies[-100:]) / len(self._latencies[-100:]) if self._latencies else 0.0
        )
        return HealthStatus(
            "market-data", alive, "ok" if alive else "heartbeat timeout", latency, now
        )

    @property
    def dropped_ticks(self) -> int:
        return self._dropped
