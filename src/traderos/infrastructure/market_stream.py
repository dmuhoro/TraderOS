"""Provider-neutral streaming market data infrastructure.

The transport is injected so the Binance implementation can be tested without
network access and future providers can share the same pipeline.

Programme B hardening:
- OT-004  tick validation: finite positive price/quantity, symbol checks,
          provider timestamp-unit normalization (Binance milliseconds vs
          seconds), and stale/future rejection with a malformed-input counter.
- OT-007  candle aggregation: explicit flush(), flush_all() and stale-symbol
          flushing; late ticks for already-closed buckets are rejected and
          counted instead of corrupting OHLC.
- OT-008  bounded retention: recorder and latency buffers stop growing.
"""

from __future__ import annotations

import json
import math
import queue
import time
import uuid
from collections import deque
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any
from typing import Protocol

from traderos.domain.ports import HealthStatus
from traderos.domain.ports import MarketDataPort

_EPOCH_SECONDS_THRESHOLD = 10_000_000_000


class InvalidTickError(ValueError):
    """Raised when a raw market-data payload fails validation (OT-004)."""


def normalize_timestamp(value: float | str, *, now: datetime | None = None) -> datetime:
    """Convert a provider epoch to UTC, auto-detecting milliseconds vs seconds.

    Binance streams publish millisecond epochs (>1e12). Interpreting those as
    seconds produced year ~33862 timestamps (OT-004).
    """
    try:
        ts = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidTickError(f"invalid timestamp {value!r}") from exc
    if not math.isfinite(ts):
        raise InvalidTickError(f"invalid timestamp {value!r}")
    if abs(ts) > _EPOCH_SECONDS_THRESHOLD:
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidTickError(f"invalid timestamp {value!r}") from exc


def validate_tick(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
    max_staleness_seconds: float = 300.0,
    max_future_seconds: float = 60.0,
) -> dict[str, Any]:
    """Validate a raw tick payload, returning normalized fields (OT-004)."""
    now = now or datetime.now(tz=UTC)
    symbol = raw.get("symbol")
    if not isinstance(symbol, str) or not symbol or any(c.isspace() for c in symbol):
        raise InvalidTickError(f"invalid symbol {symbol!r}")

    try:
        price = Decimal(str(raw["price"]))
    except (KeyError, InvalidOperation) as exc:
        raise InvalidTickError(f"invalid price {raw.get('price')!r}") from exc
    if not price.is_finite() or price <= 0:
        raise InvalidTickError(f"invalid price {raw.get('price')!r}")

    try:
        quantity = Decimal(str(raw.get("quantity", "0")))
    except InvalidOperation as exc:
        raise InvalidTickError(f"invalid quantity {raw.get('quantity')!r}") from exc
    if not quantity.is_finite() or quantity < 0:
        raise InvalidTickError(f"invalid quantity {raw.get('quantity')!r}")

    exchange = normalize_timestamp(raw.get("timestamp", now.timestamp()))
    if exchange > now + timedelta(seconds=max_future_seconds):
        raise InvalidTickError(f"future tick {exchange.isoformat()} vs now {now.isoformat()}")
    if (now - exchange).total_seconds() > max_staleness_seconds:
        raise InvalidTickError(f"stale tick {exchange.isoformat()} vs now {now.isoformat()}")

    return {
        "symbol": symbol,
        "price": price,
        "quantity": quantity,
        "exchange_timestamp": exchange,
        "source": str(raw.get("source", "binance")),
        "event_id": str(raw.get("event_id", uuid.uuid4())),
    }


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


# ---------------------------------------------------------------------------
# Binance aggregate-trade WebSocket transport (OT-001)
#
# The frame functions are pure and unit-tested offline. Live connectivity is
# injected via ``connect`` because this sandbox has no network access; the
# default connector lazily imports ``websockets`` (sync API) at connect time.
# ---------------------------------------------------------------------------

BINANCE_STREAM_URL = "wss://stream.binance.com:9443"
BINANCE_TRADE_EVENTS = frozenset({"aggTrade", "trade"})


def binance_stream_symbol(symbol: str) -> str:
    """``BTCUSDT``/``btcusdt`` -> ``btcusdt`` (Binance stream naming)."""
    return "".join(ch for ch in symbol.lower() if ch.isalnum())


def build_subscription_frame(symbols: Iterable[str]) -> str:
    """Build a Binance SUBSCRIBE frame for the symbols' aggTrade streams."""
    params = [f"{binance_stream_symbol(s)}@aggTrade" for s in symbols]
    return json.dumps({"method": "SUBSCRIBE", "params": params, "id": 1})


def build_stream_url(symbols: Iterable[str], *, base: str | None = None) -> str:
    """Binance combined-stream URL for the symbols' aggTrade streams.

    The combined-stream endpoint (``/stream?streams=...``) is reachable with a
    single connection and accepts the SUBSCRIBE frame from
    ``build_subscription_frame``. Connects to a bare base URL would 404, so the
    transport must always target this path.
    """
    base_url = (base or BINANCE_STREAM_URL).rstrip("/")
    streams = "/".join(f"{binance_stream_symbol(s)}@aggTrade" for s in symbols)
    return f"{base_url}/stream?streams={streams}"


def parse_trade_frame(text: str, *, source: str = "binance") -> dict[str, Any] | None:
    """Parse one Binance WS frame into a normalized raw tick (OT-001/OT-004).

    Handles both combined-stream envelopes (``{"stream": ..., "data": {...}}``)
    and raw trade events. Non-trade frames (SUBSCRIBE acks, heartbeats, kline
    events) return ``None`` so the caller skips them.
    """
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    event = payload.get("e")
    if isinstance(event, str) and event not in BINANCE_TRADE_EVENTS:
        return None
    if "p" not in payload:
        return None
    symbol = payload.get("s")
    if not isinstance(symbol, str) or not symbol:
        return None
    return {
        "symbol": symbol,
        "price": payload["p"],
        "quantity": payload.get("q", "0"),
        "timestamp": payload.get("T") or payload.get("E"),
        "source": source,
        "event_id": f"{source}-{payload.get('a') or payload.get('t') or '0'}",
    }


class _DefaultWebSocketConnector:
    """Lazy, injectable WebSocket opener (sync ``websockets`` client)."""

    def __call__(self, url: str) -> Any:
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as exc:
            raise RuntimeError(
                "Live Binance streaming requires the 'websockets' package: "
                "pip install 'traderos[binance]'"
            ) from exc
        return ws_connect(url)


class BinanceStreamTransport(StreamTransport):
    """Thin transport for Binance aggregate-trade streams (OT-001).

    ``connect`` is injected so tests exercise frame parsing and subscription
    without network access. A connector must return an object exposing
    ``send(str)``, ``recv()`` (returns ``None`` on clean close) and ``close()``.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self._url = url or BINANCE_STREAM_URL
        self._connector = connector or _DefaultWebSocketConnector()
        self._ws: Any = None

    def connect(self, symbols: list[str]) -> Iterable[dict[str, Any]]:
        self.close()
        url = build_stream_url(symbols, base=self._url)
        ws = self._connector(url)
        self._ws = ws
        ws.send(build_subscription_frame(symbols))
        while True:
            frame = ws.recv()
            if frame is None:
                break
            tick = parse_trade_frame(frame)
            if tick is not None:
                yield tick

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: S110 — best-effort teardown
                pass


class ClockMonitor:
    def __init__(self, max_drift_ms: float = 1000.0) -> None:
        self.max_drift_ms = max_drift_ms
        self.last_drift_ms = 0.0

    def observe(self, exchange_time: datetime, now: datetime | None = None) -> bool:
        current = now or datetime.now(tz=UTC)
        self.last_drift_ms = (current - exchange_time).total_seconds() * 1000
        return abs(self.last_drift_ms) <= self.max_drift_ms


class CandleAggregator:
    def __init__(
        self,
        interval_seconds: int = 60,
        *,
        max_idle_seconds: float | None = None,
        closed_bucket_limit: int = 4096,
    ) -> None:
        self.interval = interval_seconds
        self.max_idle_seconds = max_idle_seconds or float(interval_seconds * 10)
        self.closed_bucket_limit = closed_bucket_limit
        self._current: dict[str, list[Any]] = {}
        self._closed_starts: dict[str, deque[int]] = {}
        self.late_ticks = 0

    @staticmethod
    def _bucket_start(exchange_timestamp: datetime, interval: int) -> int:
        epoch = int(exchange_timestamp.timestamp())
        return epoch - epoch % interval

    def add(self, tick: Tick) -> Candle | None:
        start = self._bucket_start(tick.exchange_timestamp, self.interval)
        closed = self._closed_starts.get(tick.symbol)
        bucket = self._current.get(tick.symbol)
        if bucket and bucket[0] != start:
            if start < bucket[0] or (closed and start in closed):
                # Late tick for an older, already-closed bucket (OT-007).
                self.late_ticks += 1
                return None
            candle = self._make(bucket)
            self._mark_closed(tick.symbol, bucket[0])
            self._current[tick.symbol] = [start, tick]
            return candle
        if closed and start in closed:
            self.late_ticks += 1
            return None
        if not bucket:
            self._current[tick.symbol] = [start, tick]
        else:
            bucket.append(tick)
        return None

    def _mark_closed(self, symbol: str, start: int) -> None:
        closed = self._closed_starts.setdefault(symbol, deque(maxlen=self.closed_bucket_limit))
        if start not in closed:
            closed.append(start)

    def flush(self, symbol: str) -> Candle | None:
        bucket = self._current.pop(symbol, None)
        if not bucket:
            return None
        self._mark_closed(symbol, bucket[0])
        return self._make(bucket)

    def flush_all(self) -> list[Candle]:
        candles = []
        for symbol in list(self._current):
            candle = self.flush(symbol)
            if candle is not None:
                candles.append(candle)
        return candles

    def flush_stale(self, now: datetime) -> list[Candle]:
        candles = []
        for symbol, bucket in list(self._current.items()):
            last_tick = bucket[-1]
            if (now - last_tick.exchange_timestamp).total_seconds() > self.max_idle_seconds:
                candle = self.flush(symbol)
                if candle is not None:
                    candles.append(candle)
        return candles

    def _make(self, bucket: list[Any]) -> Candle:
        start, *ticks = bucket
        start_dt = datetime.fromtimestamp(start, tz=UTC)
        return Candle(
            ticks[0].symbol,
            start_dt,
            start_dt + timedelta(seconds=self.interval),
            ticks[0].price,
            max(t.price for t in ticks),
            min(t.price for t in ticks),
            ticks[-1].price,
            sum((t.quantity for t in ticks), Decimal(0)),
            len(ticks),
        )


class ReplayRecorder:
    def __init__(self, max_records: int = 100_000) -> None:
        self.max_records = max_records
        self.records: deque[str] = deque(maxlen=max_records)
        self.dropped_records = 0

    def record(self, tick: Tick) -> None:
        if len(self.records) >= self.max_records:
            self.dropped_records += 1
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
        max_staleness_seconds: float = 300.0,
        max_future_seconds: float = 60.0,
        clock: ClockMonitor | None = None,
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self.transport, self.max_queue, self.reconnect_limit, self.heartbeat_timeout = (
            transport,
            max_queue,
            reconnect_limit,
            heartbeat_timeout,
        )
        self.max_staleness_seconds = max_staleness_seconds
        self.max_future_seconds = max_future_seconds
        self.clock, self.recorder = clock or ClockMonitor(), recorder or ReplayRecorder()
        self._queue: queue.Queue[Tick] = queue.Queue(maxsize=max_queue)
        self._handler: Callable[[Tick], None] | None = None
        self._symbols: list[str] = []
        self._running = False
        self._last_tick: datetime | None = None
        self._dropped = 0
        self._malformed = 0
        self._latencies: list[float] = []

    def subscribe(self, symbols: list[str], handler: Callable[[Tick], None]) -> None:
        self._symbols, self._handler = symbols, handler

    def ingest(self, raw: dict[str, Any]) -> Tick:
        received = datetime.now(tz=UTC)
        try:
            validated = validate_tick(
                raw,
                now=received,
                max_staleness_seconds=self.max_staleness_seconds,
                max_future_seconds=self.max_future_seconds,
            )
        except InvalidTickError:
            self._malformed += 1
            raise
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
                    try:
                        self.ingest(raw)
                    except InvalidTickError:
                        # Malformed frames are skipped, never treated as a
                        # transport outage (OT-004).
                        continue
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

    @property
    def malformed_ticks(self) -> int:
        return self._malformed
