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
from datetime import timedelta
from decimal import Decimal

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.infrastructure.market_stream import Candle
from traderos.infrastructure.market_stream import CandleAggregator
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick

# Aggregator intervals (seconds) -> Binance kline interval strings. Reconcile
# only supports the intervals the exchange publishes; anything else is
# recorded as a reconcile failure rather than silently skipped.
_BINANCE_INTERVALS: dict[int, str] = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    14400: "4h",
    86400: "1d",
}


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
        reconcile_tolerance_bps: float = 5.0,
    ) -> None:
        self._stream = stream
        self._backfill = backfill
        self._aggregator = CandleAggregator(interval_seconds=interval_seconds)
        self._reconcile_tolerance_bps = reconcile_tolerance_bps
        self._lock = threading.Lock()
        self._snapshot: dict[str, list[CollectorOHLCV]] = {}
        self._subscribed: set[str] = set()
        self._ticks_seen = 0
        # Resync/reconciliation meters (Sprint 45): the stream fires
        # ``handle_reconnect`` after every proven reconnection; reconciliation
        # converges the live cache to REST/exchange truth for the outage
        # window. Every outcome is counted — never silently dropped.
        self._resyncs = 0
        self._gaps_filled = 0
        self._divergences = 0
        self._reconcile_failures = 0
        # Mop-up window (Sprint 45): the reconcile pass that runs at resync
        # time cannot verify a candle whose official kline has not matured at
        # the exchange yet. While this window is open, every newly closed
        # candle triggers one rate-limited verification pass so late-maturing
        # truth is applied as soon as it exists.
        self._mopup_until = datetime.min.replace(tzinfo=UTC)
        self._last_mopup = datetime.min.replace(tzinfo=UTC)
        self._observers: list[Callable[[Tick], None]] = []
        stream.subscribe([], self._on_tick)
        stream.on_reconnect = self.handle_reconnect

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
        mopup_due = False
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
            now = datetime.now(tz=UTC)
            mopup_due = (
                now < self._mopup_until
                and (now - self._last_mopup).total_seconds() >= self._aggregator.interval
            )
            if mopup_due:
                self._last_mopup = now
        if mopup_due:
            # Outside the lock: reconcile acquires it itself. A newly closed
            # candle means the previous minute's official kline just matured.
            try:
                self.reconcile_resync()
            except Exception:  # noqa: BLE001, S110 — metered inside, never fatal
                pass

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

    # ------------------------------------------------------------------
    # Resync reconciliation (Sprint 45 — closes G-02 "WS resync vs live
    # API untested"). After the WS stream reconnects, the live cache may
    # (a) miss candles for outage minutes entirely, and (b) hold a candle
    # that was mid-flight when the stream died, closed incomplete. This
    # method converges the cache to REST/exchange truth for the outage
    # window: missing closed intervals are filled from the REST backfill
    # collector, cached candles that diverge beyond tolerance are replaced,
    # and every outcome is metered.
    # ------------------------------------------------------------------

    def handle_reconnect(self) -> None:
        """Stream-side resync hook. Never raises into the run loop."""
        with self._lock:
            self._resyncs += 1
            self._mopup_until = datetime.now(tz=UTC) + timedelta(
                seconds=2 * self._aggregator.interval
            )
        try:
            self.reconcile_resync()
        except Exception:  # noqa: BLE001 — reconciliation is metered, never fatal
            with self._lock:
                self._reconcile_failures += 1

    @property
    def resyncs(self) -> int:
        with self._lock:
            return self._resyncs

    @property
    def reconcile_gaps_filled(self) -> int:
        with self._lock:
            return self._gaps_filled

    @property
    def reconcile_divergences(self) -> int:
        with self._lock:
            return self._divergences

    @property
    def reconcile_failures(self) -> int:
        with self._lock:
            return self._reconcile_failures

    def _binance_interval(self) -> str | None:
        return _BINANCE_INTERVALS.get(self._aggregator.interval)

    @staticmethod
    def _rel_bps(a: Decimal, b: Decimal) -> float:
        if b == 0:
            return 0.0 if a == 0 else float("inf")
        return float(abs((a - b) / b) * Decimal(10000))

    def _diverges(self, cached: CollectorOHLCV, truth: CollectorOHLCV) -> bool:
        tol = self._reconcile_tolerance_bps
        return any(
            self._rel_bps(getattr(cached, f), getattr(truth, f)) > tol
            for f in ("open", "high", "low", "close", "volume")
        )

    def reconcile_resync(self, *, limit_cap: int = 120) -> dict[str, int]:
        """Converge the live cache to exchange truth over the outage window.

        Returns ``{"gaps_filled": n, "divergences": m}``. Raises on wiring
        problems (no backfill / unsupported interval are reported by the
        failure counter via :meth:`handle_reconnect`, not raised).
        """
        interval = self._binance_interval()
        backfill = self._backfill
        if backfill is None or interval is None:
            with self._lock:
                self._reconcile_failures += 1
            return {"gaps_filled": 0, "divergences": 0}

        now = datetime.now(tz=UTC)
        gaps_filled = 0
        divergences = 0
        for symbol in sorted(self._snapshot.keys()):
            with self._lock:
                bucket = list(self._snapshot[symbol])
            if not bucket:
                continue
            oldest = bucket[0].timestamp

            window_s = int((now - oldest).total_seconds())
            needed = min(limit_cap, window_s // self._aggregator.interval + 2)
            try:
                truth = backfill.fetch_historical(symbol, interval, limit=needed)
            except Exception:  # noqa: BLE001 — REST outage must not kill reconcile chain
                with self._lock:
                    self._reconcile_failures += 1
                continue
            # Only fully-closed klines are authoritative; drop the row still
            # forming at the exchange right now. The window spans from the
            # oldest cached candle through now: outage minutes sit at the END
            # of the cache (that is where the stream died), not inside it.
            interval_delta = self._aggregator.interval
            truth = [
                t
                for t in truth
                if t.symbol.upper() == symbol.upper()
                and (now - t.timestamp).total_seconds() >= interval_delta
                and t.timestamp >= oldest
            ]
            if not truth:
                continue

            by_ts = {int(c.timestamp.timestamp()): c for c in bucket}
            merged = dict(by_ts)
            changed = False
            for t in truth:
                ts_key = int(t.timestamp.timestamp())
                cached = merged.get(ts_key)
                if cached is None:
                    merged[ts_key] = t
                    gaps_filled += 1
                    changed = True
                elif self._diverges(cached, t):
                    merged[ts_key] = t
                    divergences += 1
                    changed = True

            if changed or len(merged) != len(bucket):
                reconciled = [merged[k] for k in sorted(merged)]
                with self._lock:
                    self._snapshot[symbol] = reconciled

        with self._lock:
            self._gaps_filled += gaps_filled
            self._divergences += divergences
        return {"gaps_filled": gaps_filled, "divergences": divergences}

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
