"""Sprint 37: tick-fed asyncio trading loop.

The async shift converts the synchronous ``time.sleep`` polling daemon into an
asyncio event loop where a fresh market tick for a wired market kicks a cycle
on the REAL submission path. The broker remains synchronous but is executed in
a worker thread (``asyncio.to_thread``) so a slow broker call never blocks the
loop or other markets' cycles.

Safety properties (Constitution / execution guardrails):
- Fresh-tick-only: a stale or duplicate tick never re-triggers a cycle.
- Fail closed: a tick for an unwired symbol is never silently traded — it is
  audited, counted, and notified (no silent drops).
- No feed, no loop: ``run_forever`` refuses to idle a tick-driven loop without
  a ``ParetoWebSocketIngestor`` rather than silently doing nothing.
- Contained cycles: a failing cycle degrades health and notifies without
  killing the loop or other markets.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
from traderos.domain.ports import AuditPort
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.notification_service import NotificationService
from traderos.infrastructure.async_streaming import ParetoWebSocketIngestor
from traderos.infrastructure.market_stream import Tick

# Exceptions a single cycle may surface and that the async daemon must swallow
# so a transient subsystem failure cannot take down the whole trading loop.
_CYCLE_EXCEPTIONS = (ValueError, RuntimeError, OSError, ServiceError, InfrastructureError)


class AsyncDaemonController:
    """Asyncio trading loop driven by fresh ticks on the real submission path.

    ``handle_tick`` is the single decision point: map ``Tick.symbol`` to the
    wired market, gate on freshness, and kick the real ``CycleExecutor.run``
    cycle in a worker thread. ``run_forever`` owns the ``ParetoWebSocketIngestor``
    pipeline, feeding every validated tick into that same decision point.
    """

    def __init__(
        self,
        mode: TradingMode,
        cycle_executor: CycleExecutor,
        market_symbols: Mapping[uuid.UUID, str],
        event_bus: EventBusPort,
        health: HealthPort,
        audit: AuditPort,
        metrics: MetricsPort,
        notifications: NotificationService,
        run_manifest: ManifestPort,
        ingestor: ParetoWebSocketIngestor | None = None,
    ) -> None:
        self._mode = mode
        self._cycle_executor = cycle_executor
        self._market_symbols: dict[uuid.UUID, str] = dict(market_symbols)
        symbol_to_market: dict[str, uuid.UUID] = {}
        for market_id, symbol in self._market_symbols.items():
            if symbol in symbol_to_market:
                raise ValueError(
                    f"symbol {symbol!r} mapped to multiple markets "
                    f"{symbol_to_market[symbol]} and {market_id} — ambiguous tick routing"
                )
            symbol_to_market[symbol] = market_id
        self._symbol_to_market = symbol_to_market
        self._event_bus = event_bus
        self._health = health
        self._audit = audit
        self._metrics = metrics
        self._notifications = notifications
        self._run_manifest = run_manifest
        self._ingestor = ingestor
        self._running = False
        self._last_seen: dict[uuid.UUID, datetime] = {}
        self._cycles_run = 0
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def mode(self) -> TradingMode:
        return self._mode

    @property
    def running(self) -> bool:
        return self._running

    @property
    def market_symbols(self) -> dict[uuid.UUID, str]:
        return dict(self._market_symbols)

    @property
    def cycles_run(self) -> int:
        return self._cycles_run

    def start(self) -> None:
        self._running = True
        self._health.report_healthy("orchestrator", "started (async)")
        self._audit.record(
            "orchestrator.start", "system", "orchestrator", f"mode={self._mode.value} async"
        )
        self._notifications.info(
            "Orchestrator Started", f"Trading mode: {self._mode.value} (async)"
        )
        self._run_manifest.record("orchestrator", "start", metadata={"mode": self._mode.value})

    def stop(self) -> None:
        self._running = False
        self._health.report_healthy("orchestrator", "stopped (async)")
        self._audit.record("orchestrator.stop", "system", "orchestrator")
        self._notifications.info("Orchestrator Stopped")
        self._run_manifest.record("orchestrator", "stop")

    async def handle_tick(self, tick: Tick) -> None:
        """Route one validated tick to the real submission path (fail closed).

        - Unwired symbol: audited, counted, notified — never traded.
        - Stale or duplicate tick (received at or before the last handled one
          for that market): skipped, counted — never re-submits.
        - Fresh tick: kicks a real ``CycleExecutor.run`` cycle in a worker
          thread so the loop is never blocked by broker latency.
        """
        market_id = self._symbol_to_market.get(tick.symbol)
        if market_id is None:
            self._audit.record(
                "async.tick.unmapped",
                "system",
                "async_daemon",
                f"symbol={tick.symbol} source={tick.source}",
            )
            self._metrics.counter("async_daemon.unknown_symbol")
            self._notifications.warning(
                "Async Daemon", f"tick for unwired symbol {tick.symbol} ignored"
            )
            return
        last = self._last_seen.get(market_id)
        if last is not None and tick.received_timestamp <= last:
            self._metrics.counter("async_daemon.stale_skipped")
            return
        self._last_seen[market_id] = tick.received_timestamp
        self._metrics.counter("async_daemon.ticks")
        self._audit.record(
            "async.tick",
            "system",
            "async_daemon",
            f"market={market_id} symbol={tick.symbol} price={tick.price}",
        )
        try:
            await asyncio.to_thread(self._cycle_executor.run, market_id, float(tick.price))
        except _CYCLE_EXCEPTIONS as exc:
            self._notifications.warning("Cycle Panic", f"{market_id}: {exc}")
            self._health.report_unhealthy(f"market.{market_id}", str(exc))
            self._metrics.counter("async_daemon.cycle_panics")
            return
        self._cycles_run += 1
        self._metrics.counter("async_daemon.cycles")
        self._health.report_healthy(f"market.{market_id}", f"cycle #{self._cycles_run}")

    def on_tick(self, tick: Tick) -> None:
        """Synchronous bridge for ``ParetoWebSocketIngestor.run_pipeline``.

        Schedules the async ``handle_tick`` on the running loop so validated
        ticks flowing through the ingestor pipeline hit the same real
        submission decision point without ever blocking the loop. In-flight
        tasks are tracked so ``run_forever`` can drain them on shutdown.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self.handle_tick(tick))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def run_forever(self, shutdown_timeout: int = 30) -> None:
        """Own the ingestor pipeline until stopped.

        Fail closed: without a wired ``ParetoWebSocketIngestor`` the async
        daemon refuses to run — a tick-driven loop with no feed must not sit
        silent while claiming to trade. On stop, in-flight cycles are drained
        up to ``shutdown_timeout`` seconds (the sync loop's graceful-drain
        equivalent), then cancelled rather than abandoned mid-submit.
        """
        if self._ingestor is None:
            raise ServiceError(
                "async daemon requires a ParetoWebSocketIngestor — refusing to idle without a feed"
            )
        self.start()
        try:
            await self._ingestor.run_pipeline(self.on_tick)
        finally:
            self.stop()
            if self._pending_tasks:
                inflight = list(self._pending_tasks)
                self._pending_tasks.clear()
                _, still_pending = await asyncio.wait(inflight, timeout=shutdown_timeout)
                for task in still_pending:
                    task.cancel()
                await asyncio.gather(*inflight, return_exceptions=True)

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._mode.value,
            "running": self._running,
            "markets": len(self._market_symbols),
            "cycles_run": self._cycles_run,
            "health": self._health.summary(),
            "metrics": self._metrics.snapshot(),
        }
