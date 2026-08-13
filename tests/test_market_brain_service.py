"""Sprint 38 (Slice A): the Market Brain — a per-market, tick-fed chart watcher.

Proof-first (red). The Brain is the "chart watcher" that tells the Custom
Expert Advisor what is happening in the market and the possible moves to make.
Two contracts are pinned here before any implementation exists:

1. Fail closed: with no or insufficient data the Brain is UNKNOWN and yields NO
   moves; wired into the async daemon it means the real broker seam is never
   invoked while the market is unreadable.
2. Real signal: fed a real history of candles plus live ticks, the Brain
   produces a StateSnapshot (regime/trend stage/volatility/momentum) and ranked
   moves whose risk fraction NEVER exceeds the configured cap.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from traderos.application.async_daemon import AsyncDaemonController
from traderos.application.models import TradingMode
from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.market_brain_service import MarketBrainService  # proof target
from traderos.domain.services.market_brain_service import StateSnapshot
from traderos.infrastructure.database.connection import ThreadSafeSQLiteConnection
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


def _make_conn():
    from traderos.infrastructure.database.migration_manager import migrate

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    wrapped = ThreadSafeSQLiteConnection(conn)
    migrate(wrapped)
    return wrapped


def _tick(symbol: str = "BTCUSDT", price: str = "100.5", ts: datetime | None = None) -> Tick:
    ts = ts or datetime.now(UTC)
    return Tick(
        symbol=symbol,
        price=Decimal(price),
        quantity=Decimal("1.0"),
        exchange_timestamp=ts,
        received_timestamp=ts,
        source="binance",
        event_id=str(uuid.uuid4()),
    )


def _bearish_candles(mid: uuid.UUID, count: int = 220, start_price: float = 1000.0) -> list[Candle]:
    """Deterministic declining candles with strictly increasing timestamps."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            market_id=mid,
            ohlcv=OHLCV(
                open=Decimal(str(round(start_price - i, 4))),
                high=Decimal(str(round(start_price - i + 1, 4))),
                low=Decimal(str(round(start_price - i - 1, 4))),
                close=Decimal(str(round(start_price - i - 1, 4))),
                volume=Decimal(1000),
            ),
            timestamp=base + timedelta(days=i),
            timeframe=Timeframe.DAY_1,
        )
        for i in range(count)
    ]


def _brain(max_risk_fraction: float = 0.01, action_threshold: float = 0.4) -> MarketBrainService:
    return MarketBrainService(
        max_risk_fraction=max_risk_fraction,
        action_threshold=action_threshold,
    )


class TestMarketBrainFailClosed:
    """No data, no edge: the Brain must never fabricate a move."""

    def test_empty_brain_is_unknown_and_refuses(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        snap = brain.snapshot(mid)
        assert isinstance(snap, StateSnapshot)
        assert snap.known is False
        advice = brain.advise(mid)
        assert advice.allowed is False
        assert advice.moves == []
        assert "warming up" in advice.reason

    def test_insufficient_candles_is_unknown(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, synthetic_candles(count=10, market_id=mid))
        assert brain.snapshot(mid).known is False
        assert brain.advise(mid).allowed is False

    def test_daemon_never_reaches_broker_while_brain_unknown(self) -> None:
        """The real gate proof: while the Brain has no read, the real broker
        seam in the async daemon is NEVER invoked."""
        conn = _make_conn()
        mid = uuid.uuid4()
        metrics = SQLiteMetricsService(conn)
        broker = Mock()
        daemon = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=Mock(),
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            brain=_brain(),
        )
        daemon._cycle_executor._broker = broker
        daemon._cycle_executor.run = Mock()
        asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "100.0")))
        assert daemon._cycle_executor.run.call_count == 0  # cycle skipped
        assert metrics.get_counter("async_daemon.brain_blocks") == 1
        conn.close()


class TestMarketBrainRealSignal:
    """Fed real history + live ticks, the Brain reads the market and advises."""

    def test_bullish_history_snapshot_and_capped_advice(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, synthetic_candles(count=220, start_price=100.0, market_id=mid))
        brain.update_tick(mid, _tick("BTCUSDT", "320.0"))
        brain.update_tick(mid, _tick("BTCUSDT", "320.5"))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.regime == "trending_bullish"
        assert snap.trend_stage in ("markup", "accumulation")
        assert snap.momentum > 0
        advice = brain.advise(mid)
        assert advice.allowed is True
        assert advice.moves
        for move in advice.moves:
            assert move.direction in ("long", "short")
            assert 0.0 < move.confidence <= 1.0
            assert move.risk_fraction <= 0.01  # the hard cap, never exceeded
        assert advice.reason

    def test_bearish_history_advises_short_with_cap(self) -> None:
        brain = _brain(max_risk_fraction=0.005)
        mid = uuid.uuid4()
        brain.seed_candles(mid, _bearish_candles(mid, count=220, start_price=1000.0))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.regime == "trending_bearish"
        advice = brain.advise(mid)
        assert advice.allowed is True
        assert advice.moves[0].direction == "short"
        assert advice.moves[0].risk_fraction <= 0.005

    def test_advice_risk_fraction_never_exceeds_cap_even_in_extremes(self) -> None:
        brain = _brain(max_risk_fraction=0.02)
        mid = uuid.uuid4()
        brain.seed_candles(mid, synthetic_candles(count=400, start_price=100.0, market_id=mid))
        for i in range(50):
            brain.update_tick(mid, _tick("BTCUSDT", str(200.0 + i)))
        for move in brain.advise(mid).moves:
            assert move.risk_fraction <= 0.02

    def test_daemon_drives_real_submission_when_brain_allows(self) -> None:
        """With a readable market the async daemon runs the real cycle — and a
        brain.advice event is published for the EA to consume."""
        conn = _make_conn()
        mid = uuid.uuid4()
        brain = _brain()
        brain.seed_candles(mid, synthetic_candles(count=220, start_price=100.0, market_id=mid))
        event_bus = InMemoryEventBus()
        metrics = SQLiteMetricsService(conn)
        executor = Mock()
        executor.run.return_value = None
        daemon = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=event_bus,
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            brain=brain,
        )
        events = []
        event_bus.subscribe("brain.advice", events.append)
        asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "320.5")))
        assert executor.run.call_count == 1
        assert metrics.get_counter("async_daemon.brain_cycles") == 1
        assert events and events[0].event_type == "brain.advice"
        assert events[0].payload["allowed"] is True
        status = daemon.get_status()
        assert status["brain"]["advised"] == 1
        conn.close()


class TestMarketBrainEventFlow:
    def test_brain_advice_event_carries_state_for_the_ea(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, synthetic_candles(count=220, start_price=100.0, market_id=mid))
        advice = brain.advise(mid)
        assert advice.snapshot is not None
        assert advice.snapshot.known is True
        assert advice.snapshot.indicators.get("close", 0) > 100.0


def _daily_series(closes: list[float]) -> list[Candle]:
    """Deterministic candles with strictly increasing daily timestamps."""
    mid = uuid.uuid4()
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            market_id=mid,
            ohlcv=OHLCV(
                open=Decimal(str(round(c, 4))),
                high=Decimal(str(round(c + 1, 4))),
                low=Decimal(str(round(c - 1, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal(1000),
            ),
            timestamp=base + timedelta(days=i),
            timeframe=Timeframe.DAY_1,
        )
        for i, c in enumerate(closes)
    ]


def _oscillating_uptrend(count: int = 160) -> list[float]:
    return [100.0 + i * 0.15 + ((i % 3) * 0.5) for i in range(count)]


def _oscillating_downtrend(count: int = 160) -> list[float]:
    return [100.0 - i * 0.15 - ((i % 3) * 0.5) for i in range(count)]


def _growing_swing(count: int = 25) -> list[float]:
    return [100.0 + i * 0.5 + (i % 3) * 0.4 * i for i in range(count)]


def _accumulation_series() -> list[float]:
    closes = [100.0 - i * 0.4 for i in range(120)]
    closes[-1] += 6.0  # last-day jump above the 20-EMA, below the 50-EMA
    return closes


def _distribution_series() -> list[float]:
    closes = [100.0 + i * 0.4 for i in range(120)]
    closes[-1] -= 6.0  # last-day drop below the 20-EMA, above the 50-EMA
    return closes


class TestMarketBrainEdgeReads:
    """Every defensive branch of the Brain is pinned — nothing is left to
    chance on the real order path (fail closed, never silently)."""

    def test_single_candle_is_readable_but_ranging(self) -> None:
        brain = MarketBrainService(min_candles=1, action_threshold=0.4)
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series([100.0]))
        snap = brain.snapshot(mid)
        assert snap.known is True  # min_candles=1: readable...
        assert snap.trend_stage == "unknown"  # ...but no trend structure
        assert snap.regime == "ranging"
        advice = brain.advise(mid)
        assert advice.allowed is False
        assert "range-bound" in advice.reason  # no directional edge -> flat

    def test_flat_tape_is_ranging_and_reads_unknown_stage(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series([100.0] * 60))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.trend_stage == "unknown"  # close == 20-EMA: no stage
        assert snap.regime == "ranging"  # flat tape: no volatility, no trend
        advice = brain.advise(mid)
        assert advice.allowed is False
        assert "range-bound" in advice.reason

    def test_high_volatility_regime_when_trend_stage_unknown(self) -> None:
        brain = MarketBrainService(min_candles=20, action_threshold=0.9)
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series(_growing_swing(25)))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.trend_stage == "unknown"
        assert snap.regime == "high_volatility"  # <50 bars: no 50-EMA, so
        assert snap.volatility_percentile >= 0.8  # volatility names the regime
        assert brain.advise(mid).allowed is False

    def test_trend_below_action_threshold_is_refused(self) -> None:
        brain = MarketBrainService(min_candles=60, action_threshold=0.9)
        mid = uuid.uuid4()
        brain.seed_candles(mid, synthetic_candles(count=220, start_price=100.0, market_id=mid))
        advice = brain.advise(mid)
        assert advice.allowed is False
        assert "below action threshold" in advice.reason

    def test_accumulation_stage_reads_long(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series(_accumulation_series()))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.trend_stage == "accumulation"
        assert snap.regime == "trending_bullish"
        advice = brain.advise(mid)
        assert advice.allowed is True
        assert advice.moves[0].direction == "long"
        assert 40.0 <= advice.snapshot.rsi <= 70.0  # mid-band confidence boost

    def test_distribution_stage_reads_short(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series(_distribution_series()))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.trend_stage == "distribution"
        assert snap.regime == "trending_bearish"
        advice = brain.advise(mid)
        assert advice.allowed is True
        assert advice.moves[0].direction == "short"
        assert 30.0 <= advice.snapshot.rsi <= 60.0  # mid-band confidence boost

    def test_oscillating_uptrend_reads_long(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series(_oscillating_uptrend()))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.regime == "trending_bullish"
        assert brain.advise(mid).allowed is True

    def test_oscillating_downtrend_reads_short(self) -> None:
        brain = _brain()
        mid = uuid.uuid4()
        brain.seed_candles(mid, _daily_series(_oscillating_downtrend()))
        snap = brain.snapshot(mid)
        assert snap.known is True
        assert snap.regime == "trending_bearish"
        assert brain.advise(mid).moves[0].direction == "short"
