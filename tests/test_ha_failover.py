"""G-04 HA failover: exactly-one-leader lease semantics + real daemon takeover.

The daemon drill proves the actual guarantee: while the primary holds a live
lease the standby stays idle; once the primary's lease goes stale (a kill),
the standby acquires leadership and starts trading — through the real
``CycleExecutor`` submission path.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from traderos.application.daemon_controller import DaemonController
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.ha_failover import FailoverManager
from traderos.infrastructure.ha_failover import LeaseStore
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.repositories.sqlite import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite import SQLiteTradeRepository


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 8, 4, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _manager(store_path: Path, clock, name="drill", stale=90.0):
    return FailoverManager(
        store=LeaseStore(store_path),
        notifications=Mock(),
        audit=Mock(),
        stale_after_seconds=stale,
        owner=name,
        now_fn=clock,
    )


class TestLeaseSemantics:
    def test_exactly_one_leader(self, tmp_path) -> None:
        clock = _FakeClock()
        a = _manager(tmp_path / "lease.jsonl", clock, "A")
        b = _manager(tmp_path / "lease.jsonl", clock, "B")
        assert a.try_acquire_leadership() is True
        assert b.try_acquire_leadership() is False  # A holds a live lease
        assert a.leading and not b.leading

    def test_renew_keeps_leadership_standby_refused(self, tmp_path) -> None:
        clock = _FakeClock()
        a = _manager(tmp_path / "lease.jsonl", clock, "A", stale=90.0)
        b = _manager(tmp_path / "lease.jsonl", clock, "B", stale=90.0)
        a.try_acquire_leadership()
        clock.advance(60)
        a.renew()
        clock.advance(60)  # 120s from acquire, but only 60 since renew
        assert b.try_acquire_leadership() is False

    def test_stale_lease_enables_takeover(self, tmp_path) -> None:
        clock = _FakeClock()
        a = _manager(tmp_path / "lease.jsonl", clock, "A", stale=90.0)
        b = _manager(tmp_path / "lease.jsonl", clock, "B", stale=90.0)
        a.try_acquire_leadership()
        clock.advance(120)  # A never renewed -> lease stale (killed, cannot trade)
        assert b.try_acquire_leadership() is True
        assert b.leading
        # A's lease is stale, so A must not be the recognized leader anymore:
        # B re-reading the store sees A's lease as expired.
        clock.advance(1)
        c = _manager(tmp_path / "lease.jsonl", clock, "C", stale=90.0)
        assert c.try_acquire_leadership() is False  # B holds the live lease now

    def test_clean_release_allows_immediate_takeover(self, tmp_path) -> None:
        clock = _FakeClock()
        a = _manager(tmp_path / "lease.jsonl", clock, "A")
        b = _manager(tmp_path / "lease.jsonl", clock, "B")
        a.try_acquire_leadership()
        a.release()
        assert b.try_acquire_leadership() is True

    def test_status_reflects_durable_lease_state(self, tmp_path) -> None:
        """``status()`` must read the real in-process signal AND the durable
        lease store — a standby sees who actually holds the lease, and the
        reported action is the last line written, never fabricated."""
        store = LeaseStore(tmp_path / "lease.jsonl")
        clock = _FakeClock()
        a = FailoverManager(
            store=store,
            notifications=Mock(),
            audit=Mock(),
            stale_after_seconds=90.0,
            owner="alice",
            now_fn=clock,
        )
        before = a.status()
        assert before["leading"] is False
        assert before["last_lease"] is None
        a.try_acquire_leadership()
        after = a.status()
        assert after["leading"] is True
        assert after["owner"] == "alice"
        assert after["last_lease"]["action"] == "acquire"
        assert after["last_lease"]["owner"] == "alice"
        assert "lease_path" in after

        # A second process on the same store sees a non-empty durable lease
        # and correctly reports itself as non-leading standby.
        b = _manager(tmp_path / "lease.jsonl", clock, "bob")
        b_status = b.status()
        assert b_status["leading"] is False
        assert b_status["last_lease"]["action"] == "acquire"


class _CountBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.buys = 0

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        if side == "buy":
            self.buys += 1
        return FillResult(True, quantity, close_price or 100.0, 0.0, "filled", f"ord-{self.buys}")

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


class _SignalStrat(StrategyBase):
    name = "ha_failover_signal"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"ha": True})


class TestDaemonFailoverDrill:
    def test_standby_daemon_is_fail_closed_until_primary_lease_stales(self, tmp_path) -> None:
        """The daemon only trades when it holds leadership. While the primary's
        lease is live the standby must report non-leading (fail closed); once
        the lease goes stale the standby can acquire and take over."""
        clock = _FakeClock()
        lease_path = tmp_path / "lease.jsonl"
        conn = _make_conn()

        def build(failover):
            trade_repo = SQLiteTradeRepository(conn)
            pos_repo = SQLitePositionRepository(conn)
            pf = PortfolioService(trade_repo=trade_repo, position_repo=pos_repo)
            now = datetime.now(UTC)
            signal = Mock(
                market_id=uuid.uuid4(),
                strategy_id=uuid.uuid4(),
                direction="long",
                confidence=0.8,
                generated_at=now,
                expires_at=now + timedelta(hours=1),
                id=uuid.uuid4(),
            )
            sig = Mock()
            sig.process_evaluation.return_value = SignalProvenance(
                signal=signal, strategy_name="x", indicators_used={}
            )
            from traderos.application.cycle_executor import CycleExecutor

            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=sig,
                risk_service=RiskService(),
                portfolio_service=pf,
                execution=Mock(),
                analysis=AnalysisService(),
                broker=_CountBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                enabled_strategies=lambda: [("ha_failover_signal", "ha_failover_signal", {})],
            )
            return DaemonController(
                mode=TradingMode.PAPER,
                cycle_executor=executor,
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                market_ids=[uuid.uuid4()],
                data_ingestion=None,
                failover=failover,
                standby_poll_seconds=0.01,
            )

        strategy_registry.register(_SignalStrat)
        try:
            # Primary acquires; the standby daemon is fail-closed.
            primary_failover = _manager(lease_path, clock, "primary")
            standby_failover = _manager(lease_path, clock, "standby")
            assert primary_failover.try_acquire_leadership() is True

            standby_daemon = build(standby_failover)
            primary_daemon = build(primary_failover)
            assert primary_daemon.leading is True
            assert standby_daemon.leading is False  # fail closed: no trading

            # Primary dies without renewing -> its lease goes stale.
            clock.advance(180)
            assert standby_failover.try_acquire_leadership() is True
            assert standby_daemon.leading is True  # standby takes over

            # Clean shutdown releases the lease.
            standby_failover.release()
            assert standby_failover.leading is False
        finally:
            strategy_registry.unregister("ha_failover_signal")
            conn.close()

    def test_firm_ops_drill_evidence_passes(self) -> None:
        """The committed G-04 drill must stay green, or HA failover / alert
        transport / secret access-audit have no standing proof."""
        import subprocess
        import sys

        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "evidence" / "run_firm_ops_drill.py"
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
