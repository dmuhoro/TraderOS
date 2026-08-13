"""Sprint 37: tick-fed async daemon drives the REAL submission path.

Proof-first (red): the async shift removes the sync ``time.sleep`` loop in
favour of an asyncio event loop where a fresh market tick for a wired market
must reach the genuine broker seam exactly once — and a refused or unwired
signal must never reach it. These proofs target the real boundary: a real
``CycleExecutor`` built through the same service stack the sync suite uses,
with the real authorize path (``can_trade`` -> ``assess_trade`` ->
``authorize_order`` -> ``broker.place_market_order``) and the real broker seam.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from traderos.application.async_daemon import AsyncDaemonController  # proof target
from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities.signal import Signal
from traderos.domain.entities.signal import SignalDirection
from traderos.domain.services.risk_service import RiskAssessment
from traderos.domain.services.risk_service import TradeVerdict
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.async_streaming import ParetoWebSocketIngestor
from traderos.infrastructure.database.connection import ThreadSafeSQLiteConnection
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


class _AsyncProofStrat(StrategyBase):
    name = "async_proof"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "async proof"})


class _RecordingBroker(BrokerAdapter):
    """Real broker seam that records every market-order submission."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        self.calls.append((market_id, side, quantity, close_price))
        return FillResult(True, quantity, float(close_price or 0.0), 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_stop_order(self, market_id, side, quantity, stop_price, market_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def place_trailing_stop_order(
        self, market_id, side, quantity, trail_percent, market_price=None
    ):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def modify_order(
        self, order_id, qty=None, limit_price=None, stop_price=None, trail_percent=None
    ):
        return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


def _make_conn():
    # OT-011: the production orchestrator path shares ONE connection across
    # threads (factory.py wraps it in ThreadSafeSQLiteConnection, which
    # serializes every statement under a process-wide lock). The async daemon
    # runs cycles in worker threads, so the proof must use that same real,
    # thread-safe wrapper — a raw sqlite3.Connection is thread-bound.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    wrapped = ThreadSafeSQLiteConnection(conn)
    migrate(wrapped)
    return wrapped


def _register(name, cls):
    strategy_registry._strategies[name] = cls


def _unregister(name):
    strategy_registry._strategies.pop(name, None)


def _happy_services() -> dict:
    """Mocks that let a proof signal run all the way to the broker submission path."""
    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection.LONG,
        confidence=0.8,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    signal_service = Mock()
    signal_service.process_evaluation.return_value = SignalProvenance(
        signal=signal, strategy_name="x", indicators_used={}
    )
    risk_service = Mock()
    risk_service.can_trade.return_value = TradeVerdict(True, "")
    risk_service.kill_switch = Mock()
    risk_service.assess_trade.return_value = RiskAssessment(
        kelly_fraction=0.5,
        suggested_stop_loss=99.0,
        suggested_take_profit=102.0,
        risk_per_unit=1.0,
        max_risk_amount=200.0,
    )
    risk_service.authorize_order.return_value = TradeVerdict(True, "")
    portfolio_service = Mock()
    summary = Mock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 1.0
    return {
        "signal_service": signal_service,
        "risk_service": risk_service,
        "portfolio_service": portfolio_service,
    }


def _tick(symbol: str = "BTCUSDT", price: str = "100.0", ts: datetime | None = None) -> Tick:
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


def _build(mid: uuid.UUID, conn, broker: _RecordingBroker, *, can_trade: bool = True):
    services = _happy_services()
    if not can_trade:
        services["risk_service"].can_trade.return_value = TradeVerdict(False, "blocked")
    executor = CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=services["signal_service"],
        risk_service=services["risk_service"],
        portfolio_service=services["portfolio_service"],
        execution=Mock(),
        analysis=Mock(),
        broker=broker,
        event_bus=InMemoryEventBus(),
        health=SQLiteHealthService(conn),
        audit=SQLiteAuditService(conn),
        metrics=SQLiteMetricsService(conn),
        notifications=Mock(),
        run_manifest=SQLiteManifestService(conn),
        enabled_strategies=lambda: [("async_proof", "async_proof", {})],
    )
    daemon_args = {
        "cycle_executor": executor,
        "market_symbols": {mid: "BTCUSDT"},
        "event_bus": InMemoryEventBus(),
        "health": SQLiteHealthService(conn),
        "audit": SQLiteAuditService(conn),
        "metrics": SQLiteMetricsService(conn),
        "notifications": Mock(),
        "run_manifest": SQLiteManifestService(conn),
    }
    daemon = AsyncDaemonController(mode=TradingMode.PAPER, **daemon_args)
    return daemon, services


class TestAsyncDaemonProofRealPath:
    """A fresh tick for a wired market reaches the real broker seam exactly once."""

    def test_fresh_tick_drives_real_submission_exactly_once(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            broker = _RecordingBroker()
            daemon, _ = _build(mid, conn, broker)
            asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "100.0")))
            assert broker.calls == [(mid, "buy", 1.0, 100.0)]
        finally:
            _unregister("async_proof")
        conn.close()

    def test_duplicate_tick_does_not_self_trigger(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            broker = _RecordingBroker()
            daemon, _ = _build(mid, conn, broker)
            ts = datetime.now(UTC)
            asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "100.0", ts)))
            asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "100.0", ts)))
            assert len(broker.calls) == 1  # dedupe: stale/duplicate tick never re-submits
        finally:
            _unregister("async_proof")
        conn.close()

    def test_refused_signal_never_reaches_broker(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            broker = _RecordingBroker()
            daemon, services = _build(mid, conn, broker, can_trade=False)
            asyncio.run(daemon.handle_tick(_tick("BTCUSDT", "100.0")))
            assert broker.calls == []  # concrete proof: the real seam is never invoked
            services["risk_service"].kill_switch.record_failure.assert_not_called()
        finally:
            _unregister("async_proof")
        conn.close()

    def test_unknown_symbol_never_trades(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            metrics = SQLiteMetricsService(conn)
            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=Mock(),
                risk_service=Mock(),
                portfolio_service=Mock(),
                execution=Mock(),
                analysis=Mock(),
                broker=_RecordingBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=metrics,
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
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
            )
            asyncio.run(daemon.handle_tick(_tick("ETHUSDT", "100.0")))
            assert executor._broker.calls == []  # no silent trade on an unwired symbol
            assert metrics.get_counter("async_daemon.unknown_symbol") == 1
        finally:
            _unregister("async_proof")
        conn.close()

    def test_duplicate_symbol_mapping_fails_closed(self) -> None:
        conn = _make_conn()
        m1 = uuid.uuid4()
        m2 = uuid.uuid4()
        with pytest.raises(ValueError, match="ambiguous tick routing"):
            AsyncDaemonController(
                mode=TradingMode.PAPER,
                cycle_executor=Mock(spec=CycleExecutor),
                market_symbols={m1: "BTCUSDT", m2: "BTCUSDT"},
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
            )
        conn.close()

    def test_cycle_panic_is_contained(self) -> None:
        """A failing cycle degrades health and counts the panic but never
        escapes the loop or reaches the broker."""
        conn = _make_conn()
        mid = uuid.uuid4()
        executor = Mock(spec=CycleExecutor)
        executor.run.side_effect = RuntimeError("executor failure")
        health = SQLiteHealthService(conn)
        metrics = SQLiteMetricsService(conn)
        controller = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=health,
            audit=SQLiteAuditService(conn),
            metrics=metrics,
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        asyncio.run(controller.handle_tick(_tick("BTCUSDT", "100.0")))
        assert metrics.get_counter("async_daemon.cycle_panics") == 1
        assert health.get_status(f"market.{mid}") is False
        assert controller.running is False  # a panic never flips the loop state
        conn.close()

    def test_status_and_properties(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        controller = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=Mock(spec=CycleExecutor),
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        assert controller.mode == TradingMode.PAPER
        assert controller.running is False
        status = controller.get_status()
        assert status["mode"] == "paper"
        assert status["running"] is False
        assert status["markets"] == 1
        assert status["cycles_run"] == 0
        assert "health" in status
        assert "metrics" in status
        conn.close()


class _OneShotWS:
    def __init__(self, frames: list[object]) -> None:
        self._frames = iter(frames)

    async def recv(self) -> object | None:
        return next(self._frames, None)

    async def send(self, payload: str) -> None:
        return None

    async def close(self) -> None:
        return None


class _FrameTransport:
    def __init__(self, frames: list[object]) -> None:
        self._frames = list(frames)

    async def connect(self, symbols: list[str]):
        return _OneShotWS(self._frames)

    async def close(self) -> None:
        return None

    def parse_frame(self, frame: str) -> dict | None:
        return json.loads(frame)


def _ase_trade_frame() -> str:
    return json.dumps(
        {
            "symbol": "BTCUSDT",
            "price": 100.0,
            "quantity": 1.0,
            "timestamp": datetime.now(UTC).timestamp(),
            "source": "binance",
        }
    )


class TestAsyncDaemonIngestorWiring:
    """The real ParetoWebSocketIngestor must drive the real submission path."""

    def test_ingestor_pipeline_reaches_broker(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            broker = _RecordingBroker()
            daemon, _ = _build(mid, conn, broker)
            ingestor = ParetoWebSocketIngestor(
                _FrameTransport([_ase_trade_frame()]),
                symbols=["BTCUSDT"],
                base_backoff=0.01,
                max_backoff=0.02,
            )

            async def _drive() -> None:
                task = asyncio.create_task(ingestor.run_pipeline(daemon.on_tick))
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5.0
                while not broker.calls and loop.time() < deadline:
                    await asyncio.sleep(0.01)
                await ingestor.stop()
                await task

            asyncio.run(_drive())
            assert broker.calls == [(mid, "buy", 1.0, 100.0)]
        finally:
            _unregister("async_proof")
        conn.close()

    def test_run_forever_drives_real_submission_and_drains(self) -> None:
        """The public async loop (daemon.run_forever) owns the ingestor and
        drives the same real submission path, then drains on shutdown."""
        conn = _make_conn()
        mid = uuid.uuid4()
        _register("async_proof", _AsyncProofStrat)
        try:
            broker = _RecordingBroker()
            daemon, _ = _build(mid, conn, broker)
            ingestor = ParetoWebSocketIngestor(
                _FrameTransport([_ase_trade_frame()]),
                symbols=["BTCUSDT"],
                base_backoff=0.01,
                max_backoff=0.02,
            )
            controller = AsyncDaemonController(
                mode=TradingMode.PAPER,
                cycle_executor=daemon._cycle_executor,
                market_symbols={mid: "BTCUSDT"},
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                ingestor=ingestor,
            )

            async def _drive() -> None:
                task = asyncio.create_task(controller.run_forever())
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5.0
                while not broker.calls and loop.time() < deadline:
                    await asyncio.sleep(0.01)
                await ingestor.stop()
                await asyncio.wait_for(task, timeout=5.0)

            asyncio.run(_drive())
            assert broker.calls == [(mid, "buy", 1.0, 100.0)]
            assert controller.cycles_run == 1
        finally:
            _unregister("async_proof")
        conn.close()

    def test_run_forever_cancels_inflight_cycle_on_forced_shutdown(self) -> None:
        """A cycle still mid-flight at shutdown_timeout is cancelled, never
        left hanging or double-fired."""
        conn = _make_conn()
        mid = uuid.uuid4()

        def _slow_run(market_id, close_price, candle_time=None):
            time.sleep(0.2)
            return CycleResult(mid, 0, 0, [], 0.0, datetime.now(UTC))

        executor = Mock(spec=CycleExecutor)
        executor.run.side_effect = _slow_run
        ingestor = ParetoWebSocketIngestor(
            _FrameTransport([_ase_trade_frame()]),
            symbols=["BTCUSDT"],
            base_backoff=0.01,
            max_backoff=0.02,
        )
        controller = AsyncDaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            market_symbols={mid: "BTCUSDT"},
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            ingestor=ingestor,
        )

        async def _drive() -> None:
            task = asyncio.create_task(controller.run_forever(shutdown_timeout=0))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            while not controller._pending_tasks and loop.time() < deadline:
                await asyncio.sleep(0.005)
            await ingestor.stop()  # pipeline stops while the cycle is mid-flight
            await asyncio.wait_for(task, timeout=5.0)

        asyncio.run(_drive())
        executor.run.assert_called_once()
        assert controller._pending_tasks == set()  # drained, not orphaned
        conn.close()
