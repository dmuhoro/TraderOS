from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.alpaca_broker import AlpacaBrokerAdapter
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


class _FakeAlpacaClient:
    """Simulates Alpaca's server side for one order id.

    On the FIRST submit of a new ``client_order_id`` the order is created
    server-side and recorded, but the response is dropped (TimeoutError) —
    the exact "order placed, reply lost" retry hazard. A retry with the SAME
    ``client_order_id`` returns the existing order without creating a new one.
    """

    def __init__(self) -> None:
        self._orders: dict[str, SimpleNamespace] = {}
        self.attempts: list[tuple[str, str, float]] = []

    def submit_order(self, order_data):
        cid = order_data.client_order_id
        self.attempts.append((cid, order_data.symbol, float(order_data.qty)))
        existing = self._orders.get(cid)
        if existing is not None:
            return existing
        order = SimpleNamespace(
            id="ord-1",
            symbol=order_data.symbol,
            qty=float(order_data.qty),
            filled_qty=float(order_data.qty),
            filled_avg_price=100.0,
            client_order_id=cid,
        )
        self._orders[cid] = order
        raise TimeoutError("response dropped after server-side order creation")

    @property
    def order_count(self) -> int:
        return len(self._orders)

    def get_account(self):
        return SimpleNamespace(equity=10000.0)

    def get_all_positions(self):
        return []

    def get_orders(self, request):
        return []

    def cancel_order_by_id(self, order_id):
        return None

    def replace_order_by_id(self, order_id, order_data=None):
        return None


class _AlwaysSignal(StrategyBase):
    name = "idem_always_signal"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"gate": "on"})


def _register(name, cls):
    strategy_registry._strategies[name] = cls


def _unregister(name):
    strategy_registry._strategies.pop(name, None)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


def _signal_service():
    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection.LONG,
        confidence=0.8,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    provenance = SignalProvenance(signal=signal, strategy_name="x", indicators_used={})
    service = Mock()
    service.process_evaluation.return_value = provenance
    return service


def _executor(conn, fake_client):
    from traderos.domain.adapters.broker_adapter import BrokerAdapter

    portfolio_service = Mock()
    summary = Mock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 1.0

    broker: BrokerAdapter = AlpacaBrokerAdapter(
        api_key="dummy-api-key",
        secret_key="dummy-secret-key",
        paper=True,
        client=fake_client,
    )

    return CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=_signal_service(),
        risk_service=RiskService(),
        portfolio_service=portfolio_service,
        execution=Mock(),
        analysis=Mock(),
        broker=broker,
        event_bus=InMemoryEventBus(),
        health=SQLiteHealthService(conn),
        audit=SQLiteAuditService(conn),
        metrics=SQLiteMetricsService(conn),
        notifications=Mock(),
        run_manifest=SQLiteManifestService(conn),
        enabled_strategies=lambda: [("idem_always_signal", "idem_always_signal", {})],
    )


class TestIdempotentSubmitAtAlpacaBoundary:
    def test_timeout_after_record_creates_exactly_one_order(self) -> None:
        conn = _make_conn()
        _register("idem_always_signal", _AlwaysSignal)
        try:
            fake = _FakeAlpacaClient()
            executor = _executor(conn, fake)
            result = executor.run(uuid.uuid4(), close_price=100.0)

            assert result.trades == 1
            assert fake.order_count == 1, "duplicate order created across retry"
            assert len(fake.attempts) == 2, "expected exactly two submit attempts"
            first_id, second_id = fake.attempts[0][0], fake.attempts[1][0]
            assert first_id == second_id, "retry must reuse the same client_order_id"
            assert first_id, "client_order_id must be present on every submit"
        finally:
            _unregister("idem_always_signal")
        conn.close()

    def test_different_orders_get_different_client_order_ids(self) -> None:
        conn = _make_conn()
        _register("idem_always_signal", _AlwaysSignal)
        try:
            fake = _FakeAlpacaClient()
            executor = _executor(conn, fake)
            executor.run(uuid.uuid4(), close_price=100.0)
            executor.run(uuid.uuid4(), close_price=100.0)

            ids = {fake.attempts[0][0], fake.attempts[2][0]}
            assert len(ids) == 2, "distinct orders must not share a client_order_id"
            assert fake.order_count == 2
        finally:
            _unregister("idem_always_signal")
        conn.close()
