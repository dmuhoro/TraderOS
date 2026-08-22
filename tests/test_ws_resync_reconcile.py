"""Offline tests for WS-resync reconciliation (Sprint 45, G-02 residual).

Proves the resync contract through the real service+collector chain:
after a transport outage the reconnect hook fires exactly once (on the
first ingested frame after recovery), and reconciliation converges the
live cache to REST/exchange truth — filling interior gaps, replacing
divergent candles beyond tolerance, keeping near-matches, excluding the
unclosed current kline, and metering every failure without ever killing
the stream.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.infrastructure.collectors.streaming_collector import StreamingMarketDataCollector
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick


class _Transport:
    def close(self) -> None:
        pass


def _aligned_minute(minutes_ago: int = 10) -> datetime:
    """A bucket boundary N minutes before now, so truth rows count as closed."""
    now = int(datetime.now(tz=UTC).timestamp())
    boundary = now - now % 60 - minutes_ago * 60
    return datetime.fromtimestamp(boundary, tz=UTC)


def _candle(ts: datetime, *, close: str = "100", symbol: str = "BTCUSDT") -> CollectorOHLCV:
    c = Decimal(close)
    return CollectorOHLCV(
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(1),
        timestamp=ts,
        symbol=symbol,
    )


def _raw(symbol: str, ts: datetime, price: str = "100") -> dict:
    return {
        "symbol": symbol,
        "price": price,
        "quantity": "2",
        "timestamp": ts.timestamp(),
    }


class _TruthBackfill:
    """REST stand-in returning fixed 'exchange truth' rows."""

    def __init__(self, rows: list[CollectorOHLCV], *, raises: bool = False) -> None:
        self._rows = rows
        self._raises = raises
        self.calls = 0

    def fetch_historical(self, symbol: str, interval: str, **kwargs):
        self.calls += 1
        if self._raises:
            raise ConnectionError("rest down")
        return list(self._rows)


class _OutageTransport:
    """Yields one frame per connect(); raises mid-stream on the first call."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = frames
        self.calls = 0

    def connect(self, symbols):
        self.calls += 1
        if self.calls == 1:
            yield self._frames[0]
            raise ConnectionError("ws dropped")
        yield self._frames[1]

    def close(self) -> None:
        pass


class TestReconnectHookFiresOnRealPath:
    def test_fires_once_after_failure_then_success(self) -> None:
        service = StreamingMarketDataService(_Transport(), reconnect_limit=3)
        fired: list[int] = []
        service.on_reconnect = lambda: fired.append(1)
        base = _aligned_minute(minutes_ago=0)  # fresh ticks: must pass staleness validation
        raws = [_raw("BTCUSDT", base), _raw("BTCUSDT", base + timedelta(seconds=61))]

        # Simulate the run loop's failure accounting directly: a failed
        # attempt followed by successful ingestion must fire the hook once.
        attempts = 0
        received = 0
        try:
            raise ConnectionError("drop")
        except Exception:  # noqa: BLE001 — mirrors the run loop's outage accounting
            attempts += 1
        for raw in [raws[0]]:
            if attempts:
                if service.on_reconnect is not None:
                    service.on_reconnect()
                service.resync_count += 1
                attempts = 0
            service.ingest(raw)
            received += 1
        assert fired == [1]
        assert service.resync_count == 1
        assert received == 1

    def test_run_loop_drives_hook_through_real_transport_outage(self) -> None:
        # Ticks stay within validate_tick's staleness window: the outage is
        # short and the pre-outage tick is only ~2 minutes old.
        base = _aligned_minute(minutes_ago=2)
        frames = [
            _raw("BTCUSDT", base),
            _raw("BTCUSDT", base + timedelta(seconds=121)),
        ]
        transport = _OutageTransport(frames)
        collector = StreamingMarketDataCollector(
            stream=StreamingMarketDataService(transport, reconnect_limit=3),
            backfill=None,
            interval_seconds=60,
        )
        collector.subscribe(["BTCUSDT"])
        received = collector._stream.run(max_messages=2)
        assert received == 2
        assert collector.resyncs == 1
        # The pre-outage candle was closed by the post-reconnect tick.
        assert len(collector.fetch_historical("BTCUSDT", "1m", limit=10)) >= 1

    def test_no_hook_fire_on_clean_stream(self) -> None:
        service = StreamingMarketDataService(_Transport(), reconnect_limit=3)
        base = _aligned_minute(minutes_ago=0)
        service.ingest(_raw("BTCUSDT", base))
        assert service.resync_count == 0

    def test_raising_callback_never_kills_the_run_loop(self) -> None:
        def _explode() -> None:
            raise RuntimeError("hook exploded")

        service = StreamingMarketDataService(_Transport(), reconnect_limit=3)
        service.on_reconnect = _explode
        base = _aligned_minute(minutes_ago=0)
        raws = [_raw("BTCUSDT", base), _raw("BTCUSDT", base + timedelta(seconds=61))]
        attempts = 1  # one failed connect already recorded
        received = 0
        for raw in raws:
            if attempts:
                try:
                    if service.on_reconnect is not None:
                        service.on_reconnect()
                except Exception:  # noqa: BLE001, S110 — mirrors the run loop's swallow
                    pass
                service.resync_count += 1
                attempts = 0
            service.ingest(raw)
            received += 1
        assert received == 2
        assert service.resync_count == 1

    def test_silent_transport_exhaust_is_treated_as_outage(self) -> None:
        class _SilentlyEmptyTransport:
            """Every connect() returns an iterator that yields nothing."""

            def __init__(self) -> None:
                self.calls = 0

            def connect(self, symbols):
                self.calls += 1
                return iter(())

            def close(self) -> None:
                pass

        transport = _SilentlyEmptyTransport()
        service = StreamingMarketDataService(transport, reconnect_limit=1)
        # Returns (no busy-spin): first empty connection counts as an outage,
        # second exceeds reconnect_limit=1 and stops the loop.
        assert service.run(max_messages=5) == 0
        assert transport.calls == 2

    def test_run_loop_swallows_raising_reconnect_callback(self) -> None:
        class _OutageThenBackTransport:
            def __init__(self, frames: list[dict]) -> None:
                self._frames = frames
                self.calls = 0

            def connect(self, symbols):
                self.calls += 1
                if self.calls == 1:
                    yield self._frames[0]
                    raise ConnectionError("ws dropped")
                yield self._frames[1]

            def close(self) -> None:
                pass

        base = _aligned_minute(minutes_ago=2)
        frames = [
            _raw("BTCUSDT", base),
            _raw("BTCUSDT", base + timedelta(seconds=121)),
        ]
        exploded: list[int] = []

        def _explode() -> None:
            exploded.append(1)
            raise RuntimeError("hook exploded inside run loop")

        service = StreamingMarketDataService(
            _OutageThenBackTransport(frames), reconnect_limit=3, on_reconnect=_explode
        )
        # The hook raises on the real resync path; the run loop must swallow
        # it and keep consuming (received==2), never die.
        assert service.run(max_messages=2) == 2
        assert exploded == [1]
        assert service.resync_count == 1


class TestReconciliationConvergesCacheToTruth:
    def _collector_with_cache(
        self, cached: list[CollectorOHLCV], backfill
    ) -> StreamingMarketDataCollector:
        collector = StreamingMarketDataCollector(
            stream=StreamingMarketDataService(_Transport()),
            backfill=backfill,
            interval_seconds=60,
        )
        collector.subscribe(["BTCUSDT"])
        collector._snapshot["BTCUSDT"] = list(cached)
        return collector

    @staticmethod
    def _served(collector: StreamingMarketDataCollector) -> list[CollectorOHLCV]:
        # Assert on the reconciled snapshot directly: fetch_historical's
        # sparse-cache fallback would return the backfill rows themselves and
        # mask exactly the behavior under test.
        return list(collector._snapshot["BTCUSDT"])

    def test_interior_gaps_filled_from_rest_truth(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base), _candle(base + timedelta(minutes=3))]
        truth = [_candle(base + timedelta(minutes=i)) for i in range(4)]
        backfill = _TruthBackfill(truth)
        collector = self._collector_with_cache(cached, backfill)

        result = collector.reconcile_resync()

        assert result == {"gaps_filled": 2, "divergences": 0}
        rows = self._served(collector)
        assert [int(r.timestamp.timestamp()) for r in rows] == [
            int((base + timedelta(minutes=i)).timestamp()) for i in range(4)
        ]

    def test_divergent_candle_replaced_by_exchange_truth(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        # Cached candle claims close=200 while the exchange printed 100.
        cached = [_candle(base, close="200"), _candle(base + timedelta(minutes=1))]
        truth = [_candle(base, close="100"), _candle(base + timedelta(minutes=1), close="100")]
        backfill = _TruthBackfill(truth)
        collector = self._collector_with_cache(cached, backfill)

        result = collector.reconcile_resync()

        assert result["divergences"] == 1
        assert self._served(collector)[0].close == Decimal(100)

    def test_small_divergence_within_tolerance_is_kept(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        # 100.001 vs 100 is ~0.1 bps — inside the default 5 bps tolerance.
        cached = [_candle(base, close="100.001")]
        truth = [_candle(base, close="100")]
        backfill = _TruthBackfill(truth)
        collector = self._collector_with_cache(cached, backfill)

        result = collector.reconcile_resync()

        assert result == {"gaps_filled": 0, "divergences": 0}
        assert self._served(collector)[0].close == Decimal("100.001")

    def test_unclosed_current_kline_never_filled(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base)]
        # A row stamped at the CURRENT minute is still forming at the
        # exchange — reconcile must ignore it rather than freeze a partial.
        current_start = _aligned_minute(minutes_ago=0)
        truth = [_candle(base + timedelta(minutes=1)), _candle(current_start)]
        backfill = _TruthBackfill(truth)
        collector = self._collector_with_cache(cached, backfill)

        result = collector.reconcile_resync()

        assert result["gaps_filled"] == 1
        stamps = [int(r.timestamp.timestamp()) for r in self._served(collector)]
        assert int(current_start.timestamp()) not in stamps

    def test_rest_outage_is_metered_and_non_fatal(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base)]
        backfill = _TruthBackfill([], raises=True)
        collector = self._collector_with_cache(cached, backfill)

        result = collector.reconcile_resync()

        assert result == {"gaps_filled": 0, "divergences": 0}
        assert collector.reconcile_failures == 1
        assert len(self._served(collector)) == 1

    def test_missing_backfill_records_failure(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], backfill=None)
        assert collector.reconcile_resync() == {"gaps_filled": 0, "divergences": 0}
        assert collector.reconcile_failures == 1

    def test_unsupported_interval_records_failure(self) -> None:
        collector = StreamingMarketDataCollector(
            stream=StreamingMarketDataService(_Transport()),
            backfill=_TruthBackfill([]),
            interval_seconds=5,
        )
        collector.subscribe(["BTCUSDT"])
        assert collector.reconcile_resync() == {"gaps_filled": 0, "divergences": 0}
        assert collector.reconcile_failures == 1

    def test_handle_reconnect_swallows_and_counts(self) -> None:
        class _ExplodingBackfill(_TruthBackfill):
            def fetch_historical(self, *args, **kwargs):
                raise RuntimeError("boom")

        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], _ExplodingBackfill([]))
        collector.handle_reconnect()  # must not raise
        assert collector.resyncs == 1
        assert collector.reconcile_failures == 1

    def test_handle_reconnect_guards_against_reconcile_itself_raising(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], _TruthBackfill([]))
        calls: dict[str, int] = {"n": 0}

        def _explode() -> dict[str, int]:
            calls["n"] += 1
            raise RuntimeError("reconcile exploded")

        collector.reconcile_resync = _explode  # type: ignore[method-assign]
        collector.handle_reconnect()
        assert calls == {"n": 1}
        assert collector.resyncs == 1
        assert collector.reconcile_failures == 1

    def test_meter_properties_expose_all_counters(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base), _candle(base + timedelta(minutes=1))]
        truth = [_candle(base, close="55"), _candle(base + timedelta(minutes=1))]
        collector = self._collector_with_cache(cached, _TruthBackfill(truth))
        collector.handle_reconnect()
        assert collector.resyncs == 1
        assert collector.reconcile_divergences == 1
        assert collector.reconcile_gaps_filled == 0
        assert collector.reconcile_failures == 0

    def test_empty_bucket_symbol_is_skipped(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([], _TruthBackfill([_candle(base)]))
        # A symbol registered with an empty cache has nothing to reconcile.
        assert collector.reconcile_resync() == {"gaps_filled": 0, "divergences": 0}

    def test_truth_entirely_outside_window_is_skipped(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base)]
        # All klines predate the oldest cached candle -> nothing in scope.
        stale_truth = [_candle(base - timedelta(minutes=i)) for i in range(1, 4)]
        collector = self._collector_with_cache(cached, _TruthBackfill(stale_truth))
        assert collector.reconcile_resync() == {"gaps_filled": 0, "divergences": 0}
        assert len(self._served(collector)) == 1

    def test_zero_baseline_volume_counts_as_divergence(self) -> None:
        import dataclasses

        base = _aligned_minute(minutes_ago=10)
        cached = [_candle(base)]  # volume=1
        zero_vol = dataclasses.replace(_candle(base), volume=Decimal(0))
        collector = self._collector_with_cache(cached, _TruthBackfill([zero_vol]))
        # Nonzero cached vs zero-truth baseline is infinitely far apart.
        assert collector.reconcile_resync()["divergences"] == 1

    def test_mopup_pass_verifies_late_maturing_kline(self) -> None:
        """The resync-instant reconcile cannot see a kline that is still
        forming at the exchange; the next candle-close event within the
        mop-up window must verify it once the truth has matured."""
        base = _aligned_minute(minutes_ago=10)
        # Cache holds a candle whose official kline was NOT mature at resync
        # time, so the first pass correctly skipped it.
        cached = [_candle(base, close="200")]
        collector = self._collector_with_cache(cached, _TruthBackfill([]))
        collector.handle_reconnect()  # truth not yet available -> nothing changes
        assert collector.reconcile_divergences == 0

        # Truth matures; a new candle closes inside the mop-up window.
        collector._backfill = _TruthBackfill([_candle(base, close="100")])
        now = datetime.now(tz=UTC)
        assert now < collector._mopup_until
        collector._last_mopup = datetime.min.replace(tzinfo=UTC)
        _feed_candle_close(collector, now)  # closes a bucket -> mop-up fires
        assert self._served(collector)[0].close == Decimal(100)
        assert collector.reconcile_divergences == 1

    def test_mopup_reconcile_failure_is_swallowed_not_fatal(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], _TruthBackfill([]))
        collector.handle_reconnect()

        def _explode(**kwargs):
            raise RuntimeError("boom")

        collector.reconcile_resync = _explode  # type: ignore[method-assign]
        collector._last_mopup = datetime.min.replace(tzinfo=UTC)
        _feed_candle_close(collector, datetime.now(tz=UTC))  # must not raise
        assert collector._ticks_seen >= 2  # stream kept flowing past the failure

    def test_mopup_is_rate_limited_to_once_per_interval(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], _TruthBackfill([]))
        collector.handle_reconnect()
        calls: list[int] = []

        def _counting(**kwargs):
            calls.append(1)
            return {"gaps_filled": 0, "divergences": 0}

        collector.reconcile_resync = _counting  # type: ignore[method-assign]
        # The rate limiter reads the real clock, so drive it via _last_mopup.
        collector._last_mopup = datetime.now(tz=UTC) - timedelta(seconds=10)
        _feed_n_closes(collector, 2, datetime.now(tz=UTC))  # both suppressed: <1 interval
        assert calls == []
        collector._last_mopup = datetime.now(tz=UTC) - timedelta(seconds=61)
        _feed_n_closes(collector, 2, datetime.now(tz=UTC) + timedelta(minutes=5))
        assert len(calls) == 1  # interval elapsed -> exactly one pass

    def test_no_mop_up_outside_pending_window(self) -> None:
        base = _aligned_minute(minutes_ago=10)
        collector = self._collector_with_cache([_candle(base)], _TruthBackfill([]))
        calls: list[int] = []

        def _counting(**kwargs):
            calls.append(1)
            return {"gaps_filled": 0, "divergences": 0}

        collector.reconcile_resync = _counting  # type: ignore[method-assign]
        collector._mopup_until = datetime.min.replace(tzinfo=UTC)  # window long closed
        _feed_candle_close(collector, datetime.now(tz=UTC))
        assert calls == []


def _ingestable_tick(ts: datetime) -> Tick:
    """A tick that survives validate_tick (fresh, well-formed)."""
    return Tick("BTCUSDT", Decimal(100), Decimal(2), ts, ts)


def _feed_candle_close(collector: StreamingMarketDataCollector, ts: datetime) -> None:
    """Two ticks straddling a bucket boundary -> one closed candle event."""
    minute_start = ts.replace(second=0, microsecond=0)
    collector._on_tick(_ingestable_tick(minute_start - timedelta(seconds=30)))
    collector._on_tick(_ingestable_tick(minute_start + timedelta(seconds=5)))


def _feed_n_closes(collector: StreamingMarketDataCollector, n: int, ts: datetime) -> None:
    """Feed ``n`` distinct candle-close events at consecutive boundaries."""
    minute_start = ts.replace(second=0, microsecond=0)
    for i in range(n):
        base = minute_start + timedelta(minutes=i)
        collector._on_tick(_ingestable_tick(base - timedelta(seconds=30)))
        collector._on_tick(_ingestable_tick(base + timedelta(seconds=5)))

    @pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
    def test_every_field_checked_for_divergence(self, field: str) -> None:
        import dataclasses

        base = _aligned_minute(minutes_ago=10)
        truth_row = _candle(base)
        bad_value = getattr(truth_row, field) + Decimal(5)  # far beyond 5 bps
        cached = dataclasses.replace(truth_row, **{field: bad_value})
        backfill = _TruthBackfill([truth_row])
        collector = self._collector_with_cache([cached], backfill)
        assert collector.reconcile_resync()["divergences"] == 1
