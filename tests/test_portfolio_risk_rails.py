from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities import Position
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.flatten_service import FlattenService
from traderos.domain.services.portfolio_service import PortfolioService
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
from traderos.infrastructure.repositories.sqlite import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite import SQLiteTradeRepository


class _SpyBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.place_market_order_calls: list[tuple] = []

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
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
    name = "risk_rails_always_signal"

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


def _services(conn):
    trade_repo = SQLiteTradeRepository(conn)
    pos_repo = SQLitePositionRepository(conn)
    portfolio_service = PortfolioService(trade_repo=trade_repo, position_repo=pos_repo)
    return portfolio_service, trade_repo, pos_repo


def _executor(
    conn,
    broker,
    risk_service,
    portfolio_service,
    mode=TradingMode.PAPER,
    data_ingestion=None,
    flatten_service=None,
):
    return CycleExecutor(
        mode=mode,
        signal_service=_signal_service(),
        risk_service=risk_service,
        portfolio_service=portfolio_service,
        execution=Mock(),
        analysis=AnalysisService(),
        broker=broker,
        event_bus=InMemoryEventBus(),
        health=SQLiteHealthService(conn),
        audit=SQLiteAuditService(conn),
        metrics=SQLiteMetricsService(conn),
        notifications=Mock(),
        run_manifest=SQLiteManifestService(conn),
        data_ingestion=data_ingestion,
        enabled_strategies=lambda: [("risk_rails_always_signal", "risk_rails_always_signal", {})],
        flatten_service=flatten_service,
    )


class TestPortfolioRiskRails:
    def test_gross_exposure_cap_blocks_order_broker_never_called(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, pos_repo = _services(conn)
            mid = uuid.uuid4()
            pos_repo.add(
                Position(
                    market_id=mid, quantity=100.0, entry_price=100.0, current_price=100.0, pnl=0.0
                )
            )
            risk = RiskService(max_gross_exposure=0.504)
            broker = _SpyBroker()
            executor = _executor(conn, broker, risk, portfolio_service)
            result = executor.run(mid, close_price=100.0)
            # equity = cash(10000) + positions(10000) = 20000; cap = 0.504*20000 = 10080
            # existing exposure 10000; order notional 16*100=1600 -> 11600 > cap
            assert broker.place_market_order_calls == []
            assert result.trades == 0
            assert any("gross exposure" in e for e in result.errors)
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_allowlist_blocks_unlisted_market(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, _ = _services(conn)
            listed = uuid.uuid4()
            risk = RiskService(allowed_markets=frozenset({listed}))
            broker = _SpyBroker()
            executor = _executor(conn, broker, risk, portfolio_service)
            result = executor.run(uuid.uuid4(), close_price=100.0)
            assert broker.place_market_order_calls == []
            assert result.trades == 0
            assert any("allowlist" in e for e in result.errors)
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_allowlisted_market_reaches_broker(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, _ = _services(conn)
            mid = uuid.uuid4()
            risk = RiskService(allowed_markets=frozenset({mid}))
            broker = _SpyBroker()
            executor = _executor(conn, broker, risk, portfolio_service)
            result = executor.run(mid, close_price=100.0)
            assert broker.place_market_order_calls, "allowlisted order should reach broker"
            assert result.trades == 1
            assert not any("allowlist" in e for e in result.errors)
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_kill_switch_flattens_positions_and_blocks_loop(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, pos_repo = _services(conn)
            mid = uuid.uuid4()
            pos_repo.add(
                Position(
                    market_id=mid, quantity=5.0, entry_price=100.0, current_price=100.0, pnl=0.0
                )
            )
            risk = RiskService(kill_switch=KillSwitch())
            risk.kill_switch.engage()
            broker = _SpyBroker()
            flatten = FlattenService(
                broker=broker,
                portfolio_service=portfolio_service,
                notifications=Mock(),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
            )
            executor = _executor(conn, broker, risk, portfolio_service, flatten_service=flatten)

            first = executor.run(mid, close_price=100.0)
            # flatten issues ONE market SELL for the 5-qty position; no new buy
            sells = [c for c in broker.place_market_order_calls if c[1] == "sell"]
            buys = [c for c in broker.place_market_order_calls if c[1] == "buy"]
            assert len(sells) == 1
            assert sells[0][2] == 5.0
            assert buys == []
            assert first.trades == 0
            assert any("flattened" in e for e in first.errors)

            second = executor.run(mid, close_price=100.0)
            assert len([c for c in broker.place_market_order_calls if c[1] == "sell"]) == 1
            assert second.trades == 0
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_data_gap_blocks_trading_in_live_mode(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, _ = _services(conn)
            risk = RiskService(max_data_staleness_seconds=60.0)

            class _StaleFeed:
                def __init__(self):
                    from traderos.domain.services.backtesting_service import synthetic_candles

                    self._candles = synthetic_candles(count=10)  # timestamps 2024-01-01

                def fetch_candles(self, market_id, limit=100):
                    return self._candles

            broker = _SpyBroker()
            executor = _executor(
                conn,
                broker,
                risk,
                portfolio_service,
                mode=TradingMode.LIVE,
                data_ingestion=_StaleFeed(),
            )
            result = executor.run(uuid.uuid4(), close_price=100.0)
            assert broker.place_market_order_calls == []
            assert result.trades == 0
            assert any("trading blocked" in e or "stale" in e for e in result.errors)
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_within_limits_reaches_broker(self) -> None:
        conn = _make_conn()
        _register("risk_rails_always_signal", _AlwaysSignal)
        try:
            portfolio_service, _, _ = _services(conn)
            risk = RiskService()
            broker = _SpyBroker()
            executor = _executor(conn, broker, risk, portfolio_service)
            result = executor.run(uuid.uuid4(), close_price=100.0)
            assert broker.place_market_order_calls
            assert result.trades == 1
        finally:
            _unregister("risk_rails_always_signal")
        conn.close()

    def test_risk_rails_drill_evidence_passes(self) -> None:
        """The committed drill must stay green, or the rails' fail-closed
        guarantee has no standing proof."""
        import subprocess
        import sys

        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "evidence" / "run_risk_rails_drill.py"
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


class TestFlattenServiceEdgePaths:
    def _svc(self, broker, positions, market_prices=None):
        portfolio = Mock()
        summary = Mock()
        summary.open_positions = positions
        portfolio.get_summary.return_value = summary
        return FlattenService(
            broker=broker,
            portfolio_service=portfolio,
            notifications=Mock(),
            audit=Mock(),
            metrics=Mock(),
            market_prices=market_prices,
        )

    def _pos(self, quantity: float):
        return Position(
            market_id=uuid.uuid4(),
            quantity=quantity,
            entry_price=100.0,
            current_price=100.0,
            pnl=0.0,
        )

    def test_already_flattened_returns_cached_result(self) -> None:
        broker = _SpyBroker()
        svc = self._svc(broker, [self._pos(2.0)])
        first = svc.flatten("reason-one")
        second = svc.flatten("reason-two")
        assert first is second
        assert len(broker.place_market_order_calls) == 1
        assert second.reason == "reason-one"

    def test_skips_zero_quantity_and_uses_market_prices(self) -> None:
        broker = _SpyBroker()
        long_pos = self._pos(5.0)
        short_pos = self._pos(-3.0)
        svc = self._svc(
            broker, [self._pos(0.0), long_pos, short_pos], market_prices=lambda mid: 123.45
        )
        result = svc.flatten()
        assert result.close_orders == 2
        assert len(broker.place_market_order_calls) == 2
        sides = {market_id: side for market_id, side, _q, _p in broker.place_market_order_calls}
        assert sides[long_pos.market_id] == "sell"
        assert sides[short_pos.market_id] == "buy"
        assert all(price == 123.45 for _m, _s, _q, price in broker.place_market_order_calls)

    def test_broker_exception_records_failure_and_continues(self) -> None:
        broker = Mock()
        broker.place_flatten_order.side_effect = [
            RuntimeError("exchange down"),
            FillResult(True, 2.0, 100.0, 0.0, "filled", "o1"),
        ]
        svc = self._svc(broker, [self._pos(2.0), self._pos(2.0)])
        result = svc.flatten()
        assert result.failed_orders == 1
        assert result.close_orders == 1
        assert "exchange down" in result.errors[0]
        assert len(result.errors) == 1

    def test_unfilled_close_records_failure(self) -> None:
        broker = Mock()
        broker.place_flatten_order.return_value = FillResult(False, 0.0, 0.0, 2.0, "rejected", "")
        svc = self._svc(broker, [self._pos(2.0)])
        result = svc.flatten()
        assert result.close_orders == 0
        assert result.failed_orders == 1
        assert "not filled" in result.errors[0]
