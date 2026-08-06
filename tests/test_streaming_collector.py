"""Offline tests for the A2 streaming market-data collector (no network).

Feeds validated ticks through the streaming service's ``ingest``/``drain``
path — the exact route the live runner takes — and asserts the collector
aggregates closed candles, bounds its live window, exposes staleness for the
gap breaker, and falls back to a REST backfill for depth. The real, on-wire
quality of this seam is proven separately by the live drill
``scripts/evidence/run_real_binance_stream_drill.py``.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from urllib.request import urlopen

import pytest

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorType
from traderos.infrastructure.collectors.streaming_collector import StreamingMarketDataCollector
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick


def _binance_reachable(timeout: int = 4) -> bool:
    try:
        with urlopen("https://api.binance.com/api/v3/ping", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 — environment probe, never fatal
        return False


class _Transport:
    def close(self) -> None:
        pass


def _tick_at(symbol: str, seconds_offset: int, price: str = "100", quantity: str = "2") -> Tick:
    base = datetime.now(tz=UTC) + timedelta(seconds=seconds_offset)
    return Tick(symbol, Decimal(price), Decimal(quantity), base, base)


def _feed(
    service: StreamingMarketDataService,
    ticks: list[Tick],
) -> None:
    for tick in ticks:
        service.ingest(
            {
                "symbol": tick.symbol,
                "price": str(tick.price),
                "quantity": str(tick.quantity),
                "timestamp": tick.exchange_timestamp.timestamp(),
            }
        )
    service.drain()


def _aligned_base(interval_seconds: int = 5) -> datetime:
    """A wall-clock instant aligned to the aggregator's bucket boundary, so
    offset ticks land in deterministic buckets regardless of the run second."""
    now = datetime.now(tz=UTC)
    boundary_seconds = (now.timestamp() // interval_seconds) * interval_seconds
    return datetime.fromtimestamp(boundary_seconds, tz=UTC)


def _tick(
    symbol: str,
    offset: int,
    *,
    base: datetime,
    price: str = "100",
    quantity: str = "2",
) -> Tick:
    ts = base + timedelta(seconds=offset)
    return Tick(symbol, Decimal(price), Decimal(quantity), ts, ts)


class _EmptyBackfill:
    def fetch_historical(self, *args, **kwargs):
        return []


class _FilledBackfill:
    def fetch_historical(self, *args, **kwargs):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            CollectorOHLCV(
                open=Decimal(1),
                high=Decimal(1),
                low=Decimal(1),
                close=Decimal(1),
                volume=Decimal(0),
                timestamp=start + timedelta(hours=i),
                symbol="BTCUSDT",
            )
            for i in range(3)
        ]


class TestStreamingCollectorAggregation:
    def _make(self, backfill=None, interval_seconds: int = 5) -> StreamingMarketDataCollector:
        return StreamingMarketDataCollector(
            stream=StreamingMarketDataService(_Transport()),
            backfill=backfill,
            interval_seconds=interval_seconds,
        )

    def test_collector_type_is_streaming(self) -> None:
        assert self._make().collector_type == CollectorType.STREAMING

    def test_aggregates_closed_candles_from_ticks(self) -> None:
        collector = self._make(interval_seconds=5)
        collector.subscribe(["BTCUSDT"])
        base = _aligned_base(interval_seconds=5)
        # bucket [0,5) holds ticks at 1..3; a tick at 7 crosses into [5,10)
        # and closes bucket1 with OHLC {100,102,99,99}
        feed = [
            _tick("BTCUSDT", 1, base=base, price="100"),
            _tick("BTCUSDT", 2, base=base, price="102"),
            _tick("BTCUSDT", 3, base=base, price="99"),
            _tick("BTCUSDT", 7, base=base, price="105"),
        ]
        _feed(collector._stream, feed)
        rows = collector.fetch_historical("BTCUSDT", "1m", limit=10)
        assert len(rows) == 1
        first = rows[0]
        assert first.open == Decimal(100)
        assert first.high == Decimal(102)
        assert first.low == Decimal(99)
        assert first.close == Decimal(99)

    def test_tick_observers_see_every_tick(self) -> None:
        collector = self._make()
        collector.subscribe(["BTCUSDT"])
        seen: list[str] = []
        collector.attach_observer(lambda tick: seen.append(str(tick.price)))
        _feed(collector._stream, [_tick_at("BTCUSDT", 1, price="5")])
        assert seen == ["5"]

    def test_backfill_fills_depth_and_bounds_no_fabrication(self) -> None:
        collector = self._make(backfill=_FilledBackfill(), interval_seconds=5)
        collector.subscribe(["BTCUSDT"])
        rows = collector.fetch_historical("BTCUSDT", "1m", limit=10)
        # sparse live window (< limit//2) triggers REST backfill
        assert len(rows) == 3
        assert rows[0].symbol == "BTCUSDT"

    def test_returns_live_only_when_no_backfill(self) -> None:
        collector = self._make(backfill=None, interval_seconds=5)
        collector.subscribe(["BTCUSDT"])
        # no candles yet -> returns nothing, fails closed rather than fabricating
        assert collector.fetch_historical("BTCUSDT", "1m", limit=10) == []

    def test_stale_finite_when_fresh_but_inf_when_dry(self) -> None:
        collector = self._make(interval_seconds=5)
        collector.subscribe(["BTCUSDT"])
        assert collector.stale_seconds == float("inf")
        _feed(collector._stream, [_tick_at("BTCUSDT", 1), _tick_at("BTCUSDT", 7)])
        assert collector.stale_seconds < 60.0

    def test_unsubscribed_symbol_returns_nothing(self) -> None:
        collector = self._make(interval_seconds=5)
        collector.subscribe(["BTCUSDT"])
        _feed(collector._stream, [_tick_at("BTCUSDT", 1), _tick_at("BTCUSDT", 7)])
        assert collector.fetch_historical("ETHUSDT", "1m", limit=10) == []


@pytest.mark.skipif(
    not _binance_reachable(),
    reason="Binance not reachable — live drill skipped, not passed",
)
class TestRealStreamDrill:
    def test_real_binance_stream_drill_passes(self) -> None:
        """The committed A2 drill must stay green when the real feed is
        reachable — proving the live WebSocket seam is ordered, aggregated,
        and factory-gated (evidence in docs/evidence)."""
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "evidence"
            / "run_real_binance_stream_drill.py"
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERDICT: PASS" in proc.stdout
