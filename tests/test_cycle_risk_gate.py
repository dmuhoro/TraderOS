from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


class _SpyBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.place_market_order_calls: list[tuple] = []

    def place_market_order(self, market_id, side, quantity, close_price=None):
        self.place_market_order_calls.append((market_id, side, quantity, close_price))
        return FillResult(True, quantity, close_price or 100.0, 0.0, "filled", "ord1")

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


class _AlwaysSignal(StrategyBase):
    name = "risk_gate_always_signal"

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


def _executor(conn, risk_service: RiskService, broker: _SpyBroker):
    portfolio_service = Mock()
    summary = Mock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 100.0

    return CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=_signal_service(),
        risk_service=risk_service,
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
        enabled_strategies=lambda: [("risk_gate_always_signal", "risk_gate_always_signal", {})],
    )


class TestOrderRiskGateAtSubmissionBoundary:
    def test_order_above_max_position_size_is_refused_broker_never_called(self) -> None:
        conn = _make_conn()
        _register("risk_gate_always_signal", _AlwaysSignal)
        try:
            risk = RiskService()  # default max_position_size=0.25
            broker = _SpyBroker()
            executor = _executor(conn, risk, broker)
            result = executor.run(uuid.uuid4(), close_price=100.0)
            # notional = 100 qty * 100 price = 10_000 > 0.25 * 10_000 equity = 2_500
            assert broker.place_market_order_calls == []
            assert result.trades == 0
            assert any("order blocked" in e for e in result.errors)
        finally:
            _unregister("risk_gate_always_signal")
        conn.close()

    def test_order_after_daily_loss_breached_is_refused_broker_never_called(self) -> None:
        conn = _make_conn()
        _register("risk_gate_always_signal", _AlwaysSignal)
        try:
            risk = RiskService(kill_switch=KillSwitch())
            risk.record_realized_pnl(-250.0)  # > 2% of 10_000 equity
            broker = _SpyBroker()
            executor = _executor(conn, risk, broker)
            result = executor.run(uuid.uuid4(), close_price=100.0)
            assert broker.place_market_order_calls == []
            assert result.trades == 0
            assert any("order blocked" in e for e in result.errors)
        finally:
            _unregister("risk_gate_always_signal")
        conn.close()

    def test_order_within_limits_reaches_broker(self) -> None:
        conn = _make_conn()
        _register("risk_gate_always_signal", _AlwaysSignal)
        try:
            risk = RiskService()
            broker = _SpyBroker()
            portfolio_service = Mock()
            summary = Mock()
            summary.open_positions = []
            summary.total_equity = 10000.0
            portfolio_service.get_summary.return_value = summary
            portfolio_service.size_position.return_value = 1.0  # notional 100 <= 2500

            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=_signal_service(),
                risk_service=risk,
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
                enabled_strategies=lambda: [
                    ("risk_gate_always_signal", "risk_gate_always_signal", {})
                ],
            )
            result = executor.run(uuid.uuid4(), close_price=100.0)
            assert broker.place_market_order_calls, "within-limits order should reach broker"
            assert result.trades == 1
            assert not any("order blocked" in e for e in result.errors)
        finally:
            _unregister("risk_gate_always_signal")
        conn.close()
