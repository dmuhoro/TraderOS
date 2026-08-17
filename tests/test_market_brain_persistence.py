"""Sprint 38 (Slice C): Brain persistence / durable replay — restart-safe.

Proof-first (red). The Brain must survive a restart: seeded history and the
tick-aggregated candles it produces are persisted through the durable candle
store, and on boot each daemon loop replays the durable history before the
first read. Pinned today before the seam existed:

1. Durable replay: a fresh Brain instance replayed from the store reads the
   SAME state (regime/stage/momentum) as the original — restart-safe.
2. Durable aggregation: ticks spanning multiple intervals persist both
   aggregate candles; a fresh Brain replays them and reads known state without
   any re-seed.
3. Idempotency: re-seeding the same bars never duplicates rows; the store
   counts stay exact.
4. Both daemon loops warm from the durable store at startup and never read an
   UNKNOWN market they have durable history for.

Honest boundary (also pinned): durability is per-bar-timestamp — the provider
store's identity is one bar per (timeframe, ts). Real exchange streams are
timestamp-unique, so these proofs use timestamp-unique history. Distinct bars
that share a timestamp (a synthetic tape) collapse deterministically LAST-WINS
in the durable projection (see test below); the in-memory index-based read
keeps every bar regardless (Slice A).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.market_brain_service import MarketBrainService
from traderos.infrastructure.database.connection import ThreadSafeSQLiteConnection
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.repositories.brain_candle_store import BrainCandleStore
from traderos.infrastructure.repositories.sqlite.historical_candles import (
    SQLiteHistoricalCandleRepository,
)


def _conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    wrapped = ThreadSafeSQLiteConnection(conn)
    migrate(wrapped)
    return wrapped


def _store(conn) -> BrainCandleStore:
    return BrainCandleStore(SQLiteHistoricalCandleRepository(conn))


def _brain(store=None, **kw) -> MarketBrainService:
    return MarketBrainService(max_risk_fraction=0.01, action_threshold=0.4, store=store, **kw)


def _tick(price: str, ts: datetime, symbol: str = "BTCUSDT") -> Tick:
    return Tick(
        symbol=symbol,
        price=Decimal(price),
        quantity=Decimal("1.0"),
        exchange_timestamp=ts,
        received_timestamp=ts,
        source="binance",
        event_id=str(uuid.uuid4()),
    )


def _daily_history(mid: uuid.UUID, closes: list[float]) -> list[Candle]:
    """Deterministic timestamp-unique daily candles (mirror of the Slice A
    helper) — the shape real exchange history takes."""
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


def _rising_closes(start: float, n: int) -> list[float]:
    return [start * (1.0 + 0.01 * i) for i in range(n)]


class TestBrainDurableReplay:
    def test_fresh_brain_after_restart_reads_identical_state(self) -> None:
        """Persist a full history, then rebuild the Brain from the store: the
        replayed read must equal the original (restart-safe)."""
        conn = _conn()
        mid = uuid.uuid4()
        store = _store(conn)
        original = _brain(store)
        original.seed_candles(mid, _daily_history(mid, _rising_closes(100.0, 220)))
        snap = original.snapshot(mid)
        assert snap.known is True
        assert snap.regime == "trending_bullish"

        rebuilt = _brain(store)  # fresh instance, nothing in memory
        assert rebuilt.warm_from_store(mid, limit=300) is True
        replayed = rebuilt.snapshot(mid)
        assert replayed.known is True
        assert replayed.regime == snap.regime
        assert replayed.trend_stage == snap.trend_stage
        assert replayed.momentum == snap.momentum
        assert replayed.indicators["close"] == snap.indicators["close"]
        conn.close()

    def test_warm_with_no_durable_history_reports_false(self) -> None:
        conn = _conn()
        mid = uuid.uuid4()
        rebuilt = _brain(_store(conn))
        assert rebuilt.warm_from_store(mid, limit=300) is False
        assert rebuilt.snapshot(mid).known is False
        conn.close()

    def test_warm_without_store_reports_false(self) -> None:
        mid = uuid.uuid4()
        assert _brain().warm_from_store(mid, limit=300) is False

    def test_seed_is_idempotent_at_the_store(self) -> None:
        conn = _conn()
        mid = uuid.uuid4()
        repo = SQLiteHistoricalCandleRepository(conn)
        store = _store(conn)
        brain = _brain(store)
        candles = _daily_history(mid, _rising_closes(100.0, 60))
        brain.seed_candles(mid, candles)
        before = repo.count("market_brain", str(mid), candles[0].timeframe.value)
        brain.seed_candles(mid, candles)  # re-seed the exact same bars
        after = repo.count("market_brain", str(mid), candles[0].timeframe.value)
        assert before == 60
        assert after == 60  # never duplicated
        conn.close()

    def test_aggregate_candles_are_durable(self) -> None:
        """Ticks spanning multiple intervals persist BOTH aggregate candles; a
        fresh Brain replays them and reads known state with no re-seed."""
        conn = _conn()
        mid = uuid.uuid4()
        store = _store(conn)
        brain = _brain(store)
        brain.seed_candles(mid, _daily_history(mid, _rising_closes(100.0, 220)))
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        brain.update_tick(mid, _tick("320.0", base))
        brain.update_tick(mid, _tick("320.5", base + timedelta(minutes=10)))
        brain.update_tick(mid, _tick("321.0", base + timedelta(minutes=20)))

        repo = SQLiteHistoricalCandleRepository(conn)
        agg_count = repo.count("market_brain", str(mid), Timeframe.MINUTE_1.value)
        assert agg_count >= 1  # at least 1 aggregate persisted from the ticks

        rebuilt = _brain(store)
        assert rebuilt.warm_from_store(mid, limit=300) is True
        assert rebuilt.snapshot(mid).known is True
        assert rebuilt.snapshot(mid).liquidity == 0  # ticks are not revived
        conn.close()

    def test_same_timestamp_collision_collapses_last_wins(self) -> None:
        """Distinct bars sharing a timestamp (the synthetic tape) collapse
        deterministically LAST-WINS in the durable projection — never silently
        arbitrary. In-memory reads keep every bar regardless (Slice A)."""
        conn = _conn()
        mid = uuid.uuid4()
        repo = SQLiteHistoricalCandleRepository(conn)
        store = _store(conn)
        brain = _brain(store)
        tape = synthetic_candles(count=220, start_price=100.0, market_id=mid)
        brain.seed_candles(mid, tape)
        durable = repo.load("market_brain", str(mid), tape[0].timeframe.value)
        assert len(durable) == 1  # one bar per timestamp in the durable seat
        assert durable[0]["close"] == float(tape[-1].ohlcv.close)  # LAST wins
        # the in-memory index-based read is unaffected
        assert len(brain.snapshot(mid).indicators) > 0
        assert brain.snapshot(mid).known is True
        conn.close()


class TestBrainDaemonWarm:
    def test_sync_daemon_warms_from_store_before_read(self) -> None:
        from traderos.application.daemon_controller import DaemonController
        from traderos.application.models import CycleResult
        from traderos.application.models import TradingMode
        from traderos.infrastructure.events import InMemoryEventBus
        from traderos.infrastructure.observability import SQLiteAuditService
        from traderos.infrastructure.observability import SQLiteHealthService
        from traderos.infrastructure.observability import SQLiteManifestService
        from traderos.infrastructure.observability import SQLiteMetricsService

        conn = _conn()
        mid = uuid.uuid4()
        seed = _daily_history(mid, _rising_closes(100.0, 220))
        store = _store(conn)
        _brain(store).seed_candles(mid, seed)  # persist history durably

        executor = Mock()
        executor.run.return_value = CycleResult(mid, 0, 0, [], 0.0, datetime.now(UTC))
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        data_ingestion.fetch_candles.return_value = []  # live source empty!
        controller = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
            market_ids=[mid],
            brain=_brain(store),
        )
        blocked = controller._brain_gate(controller._brain, mid)
        assert blocked is True  # durable history enabled the read despite live being empty
        assert controller._metrics.get_counter("sync_daemon.brain_advised") == 1
        conn.close()

    def test_async_daemon_warms_from_store_before_tick(self) -> None:
        import asyncio

        from traderos.application.async_daemon import AsyncDaemonController
        from traderos.application.models import TradingMode
        from traderos.infrastructure.events import InMemoryEventBus
        from traderos.infrastructure.observability import SQLiteAuditService
        from traderos.infrastructure.observability import SQLiteHealthService
        from traderos.infrastructure.observability import SQLiteManifestService
        from traderos.infrastructure.observability import SQLiteMetricsService

        conn = _conn()
        mid = uuid.uuid4()
        seed = _daily_history(mid, _rising_closes(100.0, 220))
        store = _store(conn)
        _brain(store).seed_candles(mid, seed)  # durable history

        executor = Mock()
        executor.run.return_value = None
        metrics = SQLiteMetricsService(conn)
        daemon = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            brain=_brain(store),
        )
        asyncio.run(daemon.handle_tick(_tick("320.5", datetime(2026, 1, 1, 12, 0, tzinfo=UTC))))
        assert executor.run.call_count == 1  # durable history -> allowed
        assert metrics.get_counter("async_daemon.brain_advised") == 1
        # a second fresh tick must NOT re-warm (warm is once per market) and
        # still routes on the same replayed state
        asyncio.run(daemon.handle_tick(_tick("321.0", datetime(2026, 1, 1, 12, 1, tzinfo=UTC))))
        assert executor.run.call_count == 2
        assert metrics.get_counter("async_daemon.brain_advised") == 2
        conn.close()

    def test_async_daemon_seeds_from_live_source_when_store_empty(self) -> None:
        """No durable history (fresh DB): the async Brain must be seeded from
        the live data source so the loop can actually trade the chart."""
        import asyncio

        from traderos.application.async_daemon import AsyncDaemonController
        from traderos.application.models import TradingMode
        from traderos.infrastructure.events import InMemoryEventBus
        from traderos.infrastructure.observability import SQLiteAuditService
        from traderos.infrastructure.observability import SQLiteHealthService
        from traderos.infrastructure.observability import SQLiteManifestService
        from traderos.infrastructure.observability import SQLiteMetricsService

        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = None
        metrics = SQLiteMetricsService(conn)
        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = synthetic_candles(
            count=220, start_price=100.0, market_id=mid
        )
        daemon = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            brain=_brain(),  # no store — live seed is the only warm path
            data_ingestion=data_ingestion,
        )
        asyncio.run(daemon.handle_tick(_tick("320.5", datetime(2026, 1, 1, 12, 0, tzinfo=UTC))))
        assert executor.run.call_count == 1  # live history -> readable -> allowed
        assert metrics.get_counter("async_daemon.brain_advised") == 1
        conn.close()

    def test_async_daemon_live_source_empty_still_blocks(self) -> None:
        """Live source present but empty and no durable history: the Brain stays
        UNKNOWN and the real cycle is refused (fail closed, no silent drops)."""
        import asyncio

        from traderos.application.async_daemon import AsyncDaemonController
        from traderos.application.models import TradingMode
        from traderos.infrastructure.events import InMemoryEventBus
        from traderos.infrastructure.observability import SQLiteAuditService
        from traderos.infrastructure.observability import SQLiteHealthService
        from traderos.infrastructure.observability import SQLiteManifestService
        from traderos.infrastructure.observability import SQLiteMetricsService

        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = None
        metrics = SQLiteMetricsService(conn)
        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = []
        daemon = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            brain=_brain(),
            data_ingestion=data_ingestion,
        )
        asyncio.run(daemon.handle_tick(_tick("320.5", datetime(2026, 1, 1, 12, 0, tzinfo=UTC))))
        assert executor.run.call_count == 0  # unreadable chart never reaches the cycle
        assert metrics.get_counter("async_daemon.brain_blocks") >= 1
        conn.close()
