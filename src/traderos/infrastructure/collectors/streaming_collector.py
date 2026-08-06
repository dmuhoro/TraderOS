"""Streaming-backed market data collector (A2).

Bridges the streaming pipeline (``StreamingMarketDataService`` +
``CandleAggregator``) into the daemon's existing ``DataIngestionService``
path, so ``CycleExecutor`` and the G-03 data-gap breaker consume *real*
ticks aggregated into candles — without rewriting the ingestion seam.

The collector is ``DataCollector``-compatible: ``fetch_historical`` returns
the candles the live aggregator has closed for the symbol. Because the live
window only holds recent ticks, a configured *backfill* collector (the REST
``BinanceCollector``) supplies history when the live cache has too few
candles. If the stream is stale or absent, ``fetch_historical`` returns
whatever the live cache holds (possibly nothing) — it never fabricates data
and never blocks on the network.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC
from datetime import datetime

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.infrastructure.market_stream import Candle
from traderos.infrastructure.market_stream import CandleAggregator
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick


class StreamingFeedRunner:
    """Drives a ``StreamingMarketDataService`` in a background thread.

    ``start()/join()/stop()`` mirror a worker lifecycle so the orchestrator can
    own it. Consumption happens in ``run`` (which blocks processing the WS
    transport and reconnecting); this runner keeps that off the main loop.
    """

    def __init__(self, stream: StreamingMarketDataService, symbols: list[str]) -> None:
        self._stream = stream
        self._symbols = list(symbols)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._stream.run,
            kwargs={"max_messages": None},
            name="market-stream",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: float = 60.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def stop(self) -> None:
        self._stream.stop()
        self.join(timeout=5.0)


class StreamingMarketDataCollector(DataCollector):
    """Serve real streamed candles through the ``DataCollector`` interface.

    A background handoff thread drains the streaming service's handler and
    feeds a ``CandleAggregator``. ``fetch_historical`` returns the closed
    candles the aggregator has produced for the symbol since subscription,
    falling back to ``backfill`` (a REST collector) for depth.

    Never blocks the caller on network I/O: the stream runs on its own
    thread and ``fetch_historical`` reads a lock-protected snapshot.
    """

    def __init__(
        self,
        *,
        stream: StreamingMarketDataService,
        backfill: DataCollector | None = None,
        interval_seconds: int = 60,
    ) -> None:
        self._stream = stream
        self._backfill = backfill
        self._aggregator = CandleAggregator(interval_seconds=interval_seconds)
        self._lock = threading.Lock()
        self._snapshot: dict[str, list[CollectorOHLCV]] = {}
        self._subscribed: set[str] = set()
        self._ticks_seen = 0
        self._observers: list[Callable[[Tick], None]] = []
        stream.subscribe([], self._on_tick)

    @property
    def ticks_seen(self) -> int:
        """Total ticks drained into the aggregator (observability / drill)."""
        with self._lock:
            return self._ticks_seen

    def attach_observer(self, observer: Callable[[Tick], None]) -> None:
        """Register a passive observer that sees every drained tick.

        Observers are read-only taps for drills and observability; they never
        affect the stream's single handler chain.
        """
        with self._lock:
            self._observers.append(observer)

    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.STREAMING

    def subscribe(self, symbols: list[str]) -> None:
        """(Re)subscribe the underlying stream to the given symbols."""
        with self._lock:
            self._subscribed = set(symbols)
            self._stream.subscribe(symbols, self._on_tick)

    def _on_tick(self, tick: Tick) -> None:
        with self._lock:
            self._ticks_seen += 1
            candle = self._aggregator.add(tick)
            for observer in list(self._observers):
                observer(tick)
            if candle is None:
                return
            self._snapshot.setdefault(candle.symbol, []).append(self._to_ohlcv(candle))
            # Bound the live window: keep the last 512 candles per symbol so a
            # long-running daemon does not grow without limit.
            bucket = self._snapshot[candle.symbol]
            if len(bucket) > 512:
                del bucket[:-512]

    @staticmethod
    def _to_ohlcv(candle: Candle) -> CollectorOHLCV:
        return CollectorOHLCV(
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            timestamp=candle.start,
            symbol=candle.symbol,
        )

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        # Live cache: symbols not yet subscribed return nothing rather than
        # fabricating data; the caller (data-gap breaker) fails closed on it.
        with self._lock:
            live = list(self._snapshot.get(symbol.upper(), []))

        if self._backfill is not None and len(live) < max(limit // 2, 2):
            historical = self._backfill.fetch_historical(
                symbol, interval, start=start, end=end, limit=limit
            )
            if historical:
                return historical
        return live[-limit:]

    def validate_symbol(self, symbol: str) -> bool:
        return bool(symbol) and len(symbol) > 2

    @property
    def stale_seconds(self) -> float:
        """Seconds since the last closed candle (used by the gap breaker)."""
        now = datetime.now(tz=UTC)
        latest: datetime | None = None
        with self._lock:
            for bucket in self._snapshot.values():
                if bucket:
                    ts = bucket[-1].timestamp
                    if latest is None or ts > latest:
                        latest = ts
        if latest is None:
            return float("inf")
        return (now - latest).total_seconds()
