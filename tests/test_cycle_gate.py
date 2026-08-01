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
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService


class _MockBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None):
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

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
    name = "gate_always_signal"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"gate": "on"})


class _AlsoAlwaysSignal(StrategyBase):
    name = "gate_also_signal"

    def evaluate(self, state):
        return SignalResult("short", 0.7, {"gate": "on"})


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


def _executor(conn, enabled_strategies=None):
    from traderos.domain.services.risk_service import KillSwitch
    from traderos.domain.services.risk_service import RiskAssessment
    from traderos.domain.services.risk_service import TradeVerdict

    risk_service = Mock()
    risk_service.can_trade.return_value = TradeVerdict(True, "")
    risk_service.kill_switch = KillSwitch()
    risk_service.assess_trade.return_value = RiskAssessment(
        kelly_fraction=0.5,
        suggested_stop_loss=99.0,
        suggested_take_profit=102.0,
        risk_per_unit=1.0,
        max_risk_amount=200.0,
    )

    portfolio_service = Mock()
    summary = Mock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 1.0

    return CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=_signal_service(),
        risk_service=risk_service,
        portfolio_service=portfolio_service,
        execution=Mock(),
        analysis=Mock(),
        broker=_MockBroker(),
        event_bus=InMemoryEventBus(),
        health=SQLiteHealthService(conn),
        audit=SQLiteAuditService(conn),
        metrics=SQLiteMetricsService(conn),
        notifications=Mock(),
        run_manifest=SQLiteManifestService(conn),
        enabled_strategies=enabled_strategies,
    )


class TestCycleExecutionGate:
    def test_all_registry_strategies_run_by_default(self) -> None:
        conn = _make_conn()
        _register("gate_always_signal", _AlwaysSignal)
        _register("gate_also_signal", _AlsoAlwaysSignal)
        try:
            executor = _executor(conn)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.signals == 2
        finally:
            _unregister("gate_always_signal")
            _unregister("gate_also_signal")
        conn.close()

    def test_enabled_strategies_callable_limits_sources(self) -> None:
        conn = _make_conn()
        _register("gate_always_signal", _AlwaysSignal)
        _register("gate_also_signal", _AlsoAlwaysSignal)
        try:
            executor = _executor(
                conn, enabled_strategies=lambda: [("gate_always_signal", "gate_always_signal", {})]
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.signals == 1
        finally:
            _unregister("gate_always_signal")
            _unregister("gate_also_signal")
        conn.close()

    def test_empty_enabled_strategies_runs_none(self) -> None:
        conn = _make_conn()
        _register("gate_always_signal", _AlwaysSignal)
        try:
            executor = _executor(conn, enabled_strategies=list)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.signals == 0
        finally:
            _unregister("gate_always_signal")
        conn.close()

    def test_unknown_template_in_source_is_skipped(self) -> None:
        conn = _make_conn()
        _register("gate_always_signal", _AlwaysSignal)
        try:
            executor = _executor(
                conn,
                enabled_strategies=lambda: [
                    ("mystery", "unknown_template", {}),
                    ("gate_always_signal", "gate_always_signal", {}),
                ],
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.signals == 1
        finally:
            _unregister("gate_always_signal")
        conn.close()
