"""Sprint 38 (Slice B): the Market Brain gates the SYNC real submission path.

Proof-first (red). Slice A proved the Brain front-ends the async daemon. This
file pins the SAME fail-closed contract on the sync ``DaemonController`` — the
loop that ``TradingOrchestrator.run_forever`` drives — before any wiring exists:

1. While the Brain has no readable read, a cycle for that market is SKIPPED —
   the real ``_cycle_executor.run`` seam is never invoked (``call_count == 0``),
   ``sync_daemon.brain_blocks`` counts it, and a ``brain.advice`` event is
   published (allowed=False). No silent drop.
2. With a readable market, the real cycle runs and ``sync_daemon.brain_advised``
   counts it, with the ``brain.advice`` event carrying direction/confidence/risk.
3. Without a wired Brain, the sync loop behaves exactly as today (no-brain
   parity).
4. Config knobs reach the Brain (``market_brain.*`` -> constructed service).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

from traderos.application.daemon_controller import DaemonController  # proof target
from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.market_brain_service import MarketBrainService
from traderos.infrastructure.database.connection import ThreadSafeSQLiteConnection
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService

TS = datetime.now(UTC)


def _conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    wrapped = ThreadSafeSQLiteConnection(conn)
    migrate(wrapped)
    return wrapped


def _brain(max_risk_fraction: float = 0.01, action_threshold: float = 0.4) -> MarketBrainService:
    return MarketBrainService(
        max_risk_fraction=max_risk_fraction,
        action_threshold=action_threshold,
    )


def _controller(conn, executor, brain, market_ids, data_ingestion=None, **kw) -> DaemonController:
    kwargs = {
        "mode": TradingMode.PAPER,
        "cycle_executor": executor,
        "event_bus": InMemoryEventBus(),
        "health": SQLiteHealthService(conn),
        "audit": SQLiteAuditService(conn),
        "metrics": SQLiteMetricsService(conn),
        "notifications": Mock(),
        "run_manifest": SQLiteManifestService(conn),
        "data_ingestion": data_ingestion,
        "market_ids": market_ids,
        "brain": brain,
    }
    kwargs.update(kw)
    return DaemonController(**kwargs)


def _stopper(controller: DaemonController, delay: float = 0.25) -> threading.Thread:
    t = threading.Thread(
        target=lambda: (time.sleep(delay), setattr(controller, "_running", False)), daemon=True
    )
    t.start()
    return t


class TestMarketBrainSyncFailClosed:
    def test_unreadable_brain_never_invokes_real_cycle(self) -> None:
        """The seam proof: while the Brain has no read (no seeded history), the
        sync loop must NEVER reach ``_cycle_executor.run``."""
        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = CycleResult(mid, 0, 0, [], 0.0, TS)
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        data_ingestion.fetch_candles.return_value = []  # brain stays UNKNOWN
        event_bus = InMemoryEventBus()
        events = []
        event_bus.subscribe("brain.advice", events.append)
        controller = _controller(
            conn,
            executor,
            _brain(),
            [mid],
            data_ingestion=data_ingestion,
            event_bus=event_bus,
        )
        t = _stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert executor.run.call_count == 0  # real seam never called
        metrics = controller._metrics
        assert metrics.get_counter("sync_daemon.brain_blocks") >= 1
        assert any(e.payload["allowed"] is False for e in events)
        assert controller.get_status()["brain"]["advised"] == 0
        conn.close()

    def test_readable_brain_drives_real_cycle_once(self) -> None:
        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = CycleResult(mid, 0, 0, [], 0.0, TS)
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        data_ingestion.fetch_candles.return_value = synthetic_candles(
            count=220, start_price=100.0, market_id=mid
        )
        event_bus = InMemoryEventBus()
        events = []
        event_bus.subscribe("brain.advice", events.append)
        controller = _controller(
            conn,
            executor,
            _brain(),
            [mid],
            data_ingestion=data_ingestion,
            event_bus=event_bus,
        )
        t = _stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert executor.run.call_count >= 1  # real cycle ran
        assert controller._metrics.get_counter("sync_daemon.brain_advised") >= 1
        assert any(e.payload.get("allowed") is True for e in events)
        assert controller.get_status()["brain"]["advised"] >= 1
        conn.close()

    def test_sync_loop_parity_without_brain(self) -> None:
        """No brain wired: the sync loop must behave exactly as it always has."""
        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = CycleResult(mid, 0, 0, [], 0.0, TS)
        data_ingestion = Mock()
        data_ingestion.get_latest_close.return_value = 100.0
        controller = _controller(conn, executor, None, [mid], data_ingestion=data_ingestion)
        t = _stopper(controller)
        controller.run_forever(interval_seconds=0.05, shutdown_timeout=10)
        t.join(timeout=2)
        assert executor.run.call_count >= 1  # unchanged behaviour
        assert "brain" not in controller.get_status()
        conn.close()

    def test_brain_gate_without_data_ingestion_fails_closed(self) -> None:
        """Even with no data source at all, a wired Brain must block rather
        than let the sync loop trade an unread chart."""
        conn = _conn()
        mid = uuid.uuid4()
        executor = Mock()
        executor.run.return_value = CycleResult(mid, 0, 0, [], 0.0, TS)
        controller = _controller(conn, executor, _brain(), [mid])  # no data_ingestion
        blocked = controller._brain_gate(controller._brain, mid)
        assert blocked is False  # unreadable chart -> gate refuses the cycle
        assert controller._metrics.get_counter("sync_daemon.brain_blocks") >= 1
        conn.close()


class TestMarketBrainSyncConfig:
    def test_config_knobs_build_the_sync_brain(self) -> None:
        """``market_brain.*`` config reaches the constructed Brain (yaml/env
        path honoured); the hard risk cap is applied and never exceeded."""
        from traderos.application.factory import _build_market_brain
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(
            db_path=":memory:",
            _raw_settings={
                "market_brain": {
                    "enabled": True,
                    "min_candles": 40,
                    "action_threshold": 0.7,
                    "max_risk_fraction": 0.005,
                    "history_bars": 120,
                }
            },
        )
        brain = _build_market_brain(cfg)
        assert brain is not None
        assert brain.min_candles == 40
        assert brain.action_threshold == 0.7
        assert brain.max_risk_fraction == 0.005

    def test_config_disabled_builds_no_brain(self) -> None:
        from traderos.application.factory import _build_market_brain
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", _raw_settings={"market_brain": {"enabled": False}})
        assert _build_market_brain(cfg) is None

    def test_config_malformed_builds_no_brain(self) -> None:
        from traderos.application.factory import _build_market_brain
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(db_path=":memory:", _raw_settings={"market_brain": "not-a-dict"})
        assert _build_market_brain(cfg) is None

    def test_factory_wires_brain_into_sync_orchestrator(self) -> None:
        from traderos.application.factory import build_orchestrator
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(
            db_path=":memory:",
            _raw_settings={
                "data_collection": {
                    "forex_symbols": ["EURUSD"],
                    "crypto_symbols": ["BTCUSDT"],
                },
                "market_brain": {
                    "enabled": True,
                    "max_risk_fraction": 0.002,
                    "history_bars": 90,
                },
            },
        )
        orch = build_orchestrator(config=cfg)
        assert orch.brain is not None
        assert orch.brain.max_risk_fraction == 0.002
        assert orch.brain_history_bars == 90

    def test_factory_wires_brain_into_async_daemon(self) -> None:
        from traderos.application.factory import build_async_daemon
        from traderos.infrastructure.config.config_loader import Config

        cfg = Config(
            db_path=":memory:",
            _raw_settings={
                "data_collection": {
                    "forex_symbols": ["EURUSD"],
                    "crypto_symbols": ["BTCUSDT"],
                },
                "market_brain": {"enabled": True, "action_threshold": 0.8},
            },
        )
        daemon = build_async_daemon(config=cfg)
        assert daemon._brain is not None
        assert daemon._brain.action_threshold == 0.8
