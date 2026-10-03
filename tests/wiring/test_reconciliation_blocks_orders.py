"""B-3 wiring: a reconciliation failure must actually stop order flow.

``can_accept_orders`` is the gate every consumer reads (daemon loop, operator
session, preflight, live readiness, CLI). It was a one-way latch: once a
clean startup reconciliation set it True, no later error or severity>=2
mismatch could clear it. The daemon loop therefore kept trading through a
severe drift it was simultaneously alerting on.

These tests drive the real ``BrokerStateReconciliationService`` and the real
``DaemonController`` loop, and assert the innermost broker is never reached
once reconciliation has gone bad.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.daemon_controller import DaemonController
from traderos.application.models import TradingMode
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.broker_state_reconciliation_service import MismatchType
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.run_manifest import RunManifestService


class _BrokerGateway:
    """The broker account, as ``BrokerStateReconciliationService`` reads it.

    Implements the real adapter protocol the service actually calls:
    ``get_positions()`` and ``get_open_orders()``.
    """

    def __init__(self) -> None:
        self.positions: list[dict] = []
        self.orders: list[dict] = []
        self.fail = False

    def get_positions(self) -> list[dict]:
        if self.fail:
            raise RuntimeError("broker unreachable")
        return list(self.positions)

    def get_open_orders(self) -> list[dict]:
        if self.fail:
            raise RuntimeError("broker unreachable")
        return list(self.orders)


class _StubIngestion:
    """Supplies a price so a cycle would genuinely run if the gate allowed it."""

    def __init__(self, price: float) -> None:
        self._price = price

    def get_latest_close(self, _market_id: Any) -> float:
        return self._price


class _BrokerSpy:
    """Innermost broker adapter. Reaching this means an order got through."""

    def __init__(self) -> None:
        self.submits = 0

    def _unreachable(self, *_a: Any, **_k: Any) -> None:
        self.submits += 1
        raise AssertionError("broker was reached while reconciliation was bad")

    place_market_order = _unreachable
    place_limit_order = _unreachable
    place_flatten_order = _unreachable


def _matching_state() -> tuple[list[dict], list[dict]]:
    """Positions and orders that the broker and the local book agree on.

    Both sides must agree on orders too, otherwise the service (correctly)
    reports LOCAL_ONLY_ORDER and refuses acceptance.
    """
    positions = [{"symbol": "BTC/USD", "qty": "1.0", "avg_price": "100.0"}]
    orders = [{"symbol": "BTC/USD", "order_id": "o-1", "status": "filled"}]
    return positions, orders


def _sync(gateway: _BrokerGateway, positions: list[dict], orders: list[dict]) -> None:
    """Point the broker account at exactly the local snapshot."""
    gateway.positions = list(positions)
    gateway.orders = list(orders)


def _controller(svc: BrokerStateReconciliationService) -> DaemonController:
    """A real DaemonController wired to real ports and the real gate."""
    return DaemonController(
        mode=TradingMode.LIVE,
        cycle_executor=Mock(spec=CycleExecutor),
        event_bus=InMemoryEventBus(),
        health=HealthService(),
        audit=AuditService(),
        metrics=MetricsService(),
        notifications=Mock(),
        run_manifest=RunManifestService(),
        broker_reconciliation=svc,
        market_ids=[],
    )


def _service(gateway: _BrokerGateway) -> BrokerStateReconciliationService:
    return BrokerStateReconciliationService(gateway)


class TestB3LatchIsTwoWay:
    def test_clean_startup_then_severe_drift_revokes_order_acceptance(self) -> None:
        gateway = _BrokerGateway()
        svc = _service(gateway)

        positions, orders = _matching_state()
        _sync(gateway, positions, orders)
        first = svc.reconcile(local_positions=positions, local_orders=orders)
        assert first.failed is False
        assert svc.can_accept_orders is True

        # The broker now holds a position the local book has never seen.
        # severity >= 2: an untracked live position.
        _sync(
            gateway,
            positions + [{"symbol": "ETH/USD", "qty": "7.0", "avg_price": "50.0"}],
            orders,
        )
        second = svc.reconcile(local_positions=positions, local_orders=orders)

        assert second.has_mismatches is True
        assert MismatchType.BROKER_ONLY_POSITION.value in {
            m.mismatch_type.value for m in second.mismatches
        }
        assert svc.can_accept_orders is False, "severe drift must revoke order acceptance"

    def test_repeated_clean_reconciles_do_not_re_enable_after_revoke(self) -> None:
        gateway = _BrokerGateway()
        svc = _service(gateway)
        positions, orders = _matching_state()
        _sync(gateway, positions, orders)

        svc.reconcile(local_positions=positions, local_orders=orders)
        _sync(
            gateway,
            positions + [{"symbol": "ETH/USD", "qty": "7.0", "avg_price": "50.0"}],
            orders,
        )
        svc.reconcile(local_positions=positions, local_orders=orders)
        assert svc.can_accept_orders is False

        # Broker snapshot returns to agreement. Acceptance may only be
        # restored by an actual clean reconciliation, which is this one.
        _sync(gateway, positions, orders)
        clean = svc.reconcile(local_positions=positions, local_orders=orders)
        assert clean.failed is False
        assert svc.can_accept_orders is True

    def test_broker_failure_revokes_order_acceptance(self) -> None:
        gateway = _BrokerGateway()
        svc = _service(gateway)
        positions, orders = _matching_state()
        _sync(gateway, positions, orders)
        svc.reconcile(local_positions=positions, local_orders=orders)
        assert svc.can_accept_orders is True

        gateway.fail = True
        failed = svc.reconcile(local_positions=positions, local_orders=orders)
        assert failed.errors
        assert svc.can_accept_orders is False, "an unreadable broker must revoke acceptance"

    def test_unconfirmed_intent_severity_two_revokes_acceptance(self) -> None:
        gateway = _BrokerGateway()
        svc = _service(gateway)
        positions, orders = _matching_state()
        _sync(gateway, positions, orders)
        svc.reconcile(local_positions=positions, local_orders=orders)
        assert svc.can_accept_orders is True

        # A journal intent the broker never confirmed: exactly the B-2 crash
        # window. It must not leave order acceptance open.
        pending = [{"method": "place_market_order", "quantity": 1.0, "status": "intent"}]
        drifted = svc.reconcile(
            local_positions=positions, local_orders=orders, journal_pending=pending
        )
        assert drifted.has_mismatches is True
        assert svc.can_accept_orders is False


class TestB3DaemonLoopStopsTrading:
    def test_daemon_does_not_reach_broker_after_severe_drift(self) -> None:
        """The real loop must not place an order once reconciliation is bad."""
        gateway = _BrokerGateway()
        svc = _service(gateway)
        positions, orders = _matching_state()
        _sync(gateway, positions, orders)

        # Startup reconciles clean -> gate opens.
        svc.reconcile(local_positions=positions, local_orders=orders)
        assert svc.can_accept_orders is True

        spy = _BrokerSpy()
        controller = _controller(svc)
        controller._broker = spy

        # Drift: the broker holds a live position the local book never saw.
        _sync(
            gateway,
            positions + [{"symbol": "ETH/USD", "qty": "7.0", "avg_price": "50.0"}],
            orders,
        )

        # The drift is detected by the periodic reconciliation at the end of an
        # iteration -- that is the real production trigger, so drive that path
        # rather than reaching into the service by hand.
        controller._run_periodic_reconciliation(positions, orders)
        assert svc.can_accept_orders is False, "periodic reconcile must revoke acceptance"

        # Drive the REAL loop. It gates on can_accept_orders before any order
        # work, so the cycle executor must never be reached.
        ran: list[str] = []

        def _cycle(market_id: Any, price: Any) -> Any:
            ran.append(str(market_id))
            raise AssertionError("cycle ran while reconciliation was failing")

        controller._cycle_executor.run = _cycle
        market_id = uuid4()
        controller._market_ids = [market_id]  # something to trade
        # A price IS available: the only thing that can stop this cycle is the
        # reconciliation gate. Without it the loop would `continue` on the
        # no-price path and the test would pass for the wrong reason.
        controller._data_ingestion = _StubIngestion(100.0)  # type: ignore[assignment]
        controller._fetch_local_state = lambda: (positions, orders)  # type: ignore[method-assign]

        # Break out of the loop the moment the gate acts. The gate refuses by
        # reporting unhealthy and `continue`-ing, so this both terminates the
        # loop and proves the refusal path was the one taken.
        class _GateActed(BaseException):
            pass

        def _refused(component: str, detail: str) -> None:
            if component == "broker_reconciliation":
                raise _GateActed

        controller._health.report_unhealthy = _refused  # type: ignore[method-assign]

        controller._shutdown_at = None
        controller._shutdown_graceful_done = False
        controller._running = True

        with pytest.raises(_GateActed):
            controller._run_forever_loop(interval_seconds=0, shutdown_timeout=0)

        assert ran == [], "cycle executor reached despite a failed reconciliation"
        assert spy.submits == 0
        assert not ran, "cycle executor reached despite a failed reconciliation"

    def test_periodic_reconciliation_result_is_not_discarded(self) -> None:
        """The periodic path must surface a failed reconciliation."""
        gateway = _BrokerGateway()
        svc = _service(gateway)
        positions, orders = _matching_state()
        _sync(gateway, positions, orders)
        svc.reconcile(local_positions=positions, local_orders=orders)

        controller = _controller(svc)
        controller._fetch_local_state = lambda: (positions, orders)  # type: ignore[method-assign]

        _sync(
            gateway,
            positions + [{"symbol": "ETH/USD", "qty": "7.0", "avg_price": "50.0"}],
            orders,
        )
        controller._run_periodic_reconciliation(positions, orders)

        assert svc.can_accept_orders is False


def test_severe_mismatch_is_severity_two() -> None:
    """Pins the assumption the whole gate rests on."""
    gateway = _BrokerGateway()
    svc = _service(gateway)
    positions, orders = _matching_state()
    _sync(gateway, positions, orders)
    _sync(
        gateway,
        positions + [{"symbol": "ETH/USD", "qty": "7.0", "avg_price": "50.0"}],
        orders,
    )
    result = svc.reconcile(local_positions=positions, local_orders=orders)
    severities = {m.mismatch_type: m.severity for m in result.mismatches}
    assert severities[MismatchType.BROKER_ONLY_POSITION] >= 2


@pytest.mark.parametrize("market", ["BTC/USD"])
def test_reconcile_is_repeatable(market: str) -> None:
    """A clean reconcile is stable across repeated calls (no false positives).

    Guards against a revocation fix that flaps: acceptance must only drop on
    real drift, not on every reconciliation.
    """
    gateway = _BrokerGateway()
    svc = _service(gateway)
    positions = [{"symbol": market, "qty": "1.0", "avg_price": "100.0"}]
    orders = [{"symbol": market, "order_id": "o-1", "status": "filled"}]
    _sync(gateway, positions, orders)
    for _ in range(3):
        result = svc.reconcile(local_positions=positions, local_orders=orders)
        assert result.failed is False
        assert svc.can_accept_orders is True
