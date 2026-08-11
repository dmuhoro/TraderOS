"""G-05 causal replay: prove the per-fill chain is reconstructed from the REAL
CycleExecutor's durable records (audit hash-chain + trades table) and that
per-fill realized PnL is recomputed with FIFO matching."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

import pytest

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities.signal import Signal
from traderos.domain.entities.signal import SignalDirection
from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.ports import AuditEntry
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.replay_service import ReplayService
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskAssessment
from traderos.domain.services.risk_service import TradeVerdict
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.repositories.sqlite.trades import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite.trades import SQLiteTradeRepository


class _FillAtPriceBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.submissions: list[tuple] = []

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        price = close_price if close_price is not None else 100.0
        self.submissions.append((str(market_id), side, quantity, price))
        return FillResult(True, quantity, price, 0.0, "filled", f"ord-{side}-{price}")

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


class _ReplayStrat(StrategyBase):
    name = "test_replay_strat"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "replay"})


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


def _register():
    strategy_registry._strategies["test_replay_strat"] = _ReplayStrat


def _unregister():
    strategy_registry._strategies.pop("test_replay_strat", None)


def _provenance(direction, confidence=0.8):
    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=direction,
        confidence=confidence,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return SignalProvenance(signal=signal, strategy_name="good", indicators_used={})


class TestReplayService:
    def test_real_executor_recorded_day_replays_chain_with_realized_pnl(self) -> None:
        conn = _make_conn()
        _register()
        try:
            audit = SQLiteAuditService(conn)
            trade_repo = SQLiteTradeRepository(conn)
            position_repo = SQLitePositionRepository(conn)
            portfolio = PortfolioService(
                trade_repo=trade_repo, position_repo=position_repo, audit=audit
            )

            signal_service = Mock()
            signal_service.process_evaluation.return_value = _provenance(SignalDirection.LONG)
            risk = Mock()
            risk.can_trade.return_value = TradeVerdict(True, "")
            risk.kill_switch = KillSwitch()
            risk.assess_trade.return_value = RiskAssessment(
                kelly_fraction=0.5,
                suggested_stop_loss=99.0,
                suggested_take_profit=102.0,
                risk_per_unit=1.0,
                max_risk_amount=200.0,
            )
            risk.authorize_order.return_value = Mock(allowed=True, reason="")

            broker = _FillAtPriceBroker()
            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=signal_service,
                risk_service=risk,
                portfolio_service=portfolio,
                execution=Mock(),
                analysis=AnalysisService(),
                broker=broker,
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=audit,
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                enabled_strategies=lambda: [("good", "test_replay_strat", {})],
            )

            start = datetime.now(UTC) - timedelta(seconds=5)
            mid = uuid.uuid4()
            result1 = executor.run(mid, 100.0)
            assert result1.errors == []
            assert result1.trades == 1

            signal_service.process_evaluation.return_value = _provenance(SignalDirection.SHORT)
            result2 = executor.run(mid, 110.0)
            assert result2.errors == []
            assert result2.trades == 1
            end = datetime.now(UTC) + timedelta(seconds=5)

            replay = ReplayService(audit=audit, trade_repo=trade_repo).replay_day(start, end)

            assert replay.total_fills == 2
            fills = [c.fill for c in replay.chains if c.complete]
            entry = next(f for f in fills if f.side == "buy")
            exit_ = next(f for f in fills if f.side == "sell")

            assert entry.order_status == "filled"
            assert exit_.decision == "allowed"
            expected = exit_.filled_qty * (exit_.filled_price - entry.price)
            assert exit_.realized_pnl == pytest.approx(expected)
            assert replay.total_realized_pnl == pytest.approx(expected)
        finally:
            _unregister()
        conn.close()

    def test_blocked_decision_surfaces_as_blocked_chain_without_fill(self) -> None:
        conn = _make_conn()
        _register()
        try:
            audit = SQLiteAuditService(conn)
            portfolio = PortfolioService(
                trade_repo=SQLiteTradeRepository(conn),
                position_repo=SQLitePositionRepository(conn),
                audit=audit,
            )
            signal_service = Mock()
            signal_service.process_evaluation.return_value = _provenance(SignalDirection.LONG)
            risk = Mock()
            risk.can_trade.return_value = TradeVerdict(True, "")
            risk.kill_switch = KillSwitch()
            risk.assess_trade.return_value = RiskAssessment(
                kelly_fraction=0.5,
                suggested_stop_loss=99.0,
                suggested_take_profit=102.0,
                risk_per_unit=1.0,
                max_risk_amount=200.0,
            )
            risk.authorize_order.return_value = Mock(
                allowed=False, reason="gross exposure cap breached"
            )

            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=signal_service,
                risk_service=risk,
                portfolio_service=portfolio,
                execution=Mock(),
                analysis=AnalysisService(),
                broker=_FillAtPriceBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=audit,
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                enabled_strategies=lambda: [("good", "test_replay_strat", {})],
            )

            start = datetime.now(UTC) - timedelta(seconds=5)
            result = executor.run(uuid.uuid4(), 100.0)
            end = datetime.now(UTC) + timedelta(seconds=5)

            assert result.trades == 0
            assert any("order blocked" in e for e in result.errors)

            replay = ReplayService(audit=audit, trade_repo=portfolio.trade_repo).replay_day(
                start, end
            )
            assert replay.total_fills == 0
            assert replay.total_blocked == 1
            chain = next(c for c in replay.chains if c.blocked)
            assert chain.fill is None
            assert chain.steps[0].action == "signal.generated"
            assert chain.steps[1].action == "decision.made"
            assert chain.steps[1].detail["outcome"] == "blocked"
            assert "gross exposure" in chain.steps[1].detail["reason"]
        finally:
            _unregister()
        conn.close()

    def test_fifo_realized_pnl_short_position(self) -> None:
        now = datetime.now(UTC)
        short_open = Trade(
            id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.SELL,
            quantity=5.0,
            price=100.0,
            status=TradeStatus.FILLED,
            created_at=now,
        )
        short_open.filled_quantity = 5.0
        short_open.filled_price = 100.0
        close_short = Trade(
            id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            market_id=short_open.market_id,
            side=TradeSide.BUY,
            quantity=5.0,
            price=90.0,
            status=TradeStatus.FILLED,
            created_at=now + timedelta(minutes=1),
        )
        close_short.filled_quantity = 5.0
        close_short.filled_price = 90.0
        realized = ReplayService._fifo_realized_pnl([short_open, close_short])
        assert realized[str(close_short.id)] == pytest.approx(5.0 * (100.0 - 90.0))

    def test_fifo_realized_pnl_partial_exit(self) -> None:
        now = datetime.now(UTC)
        entry = Trade(
            id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=10.0,
            price=50.0,
            status=TradeStatus.FILLED,
            created_at=now,
        )
        entry.filled_quantity = 10.0
        entry.filled_price = 50.0
        exit1 = Trade(
            id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            market_id=entry.market_id,
            side=TradeSide.SELL,
            quantity=4.0,
            price=60.0,
            status=TradeStatus.FILLED,
            created_at=now + timedelta(minutes=1),
        )
        exit1.filled_quantity = 4.0
        exit1.filled_price = 60.0
        exit2 = Trade(
            id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            market_id=entry.market_id,
            side=TradeSide.SELL,
            quantity=6.0,
            price=70.0,
            status=TradeStatus.FILLED,
            created_at=now + timedelta(minutes=2),
        )
        exit2.filled_quantity = 6.0
        exit2.filled_price = 70.0
        realized = ReplayService._fifo_realized_pnl([entry, exit1, exit2])
        assert realized[str(exit1.id)] == pytest.approx(4.0 * (60.0 - 50.0))
        assert realized[str(exit2.id)] == pytest.approx(6.0 * (70.0 - 50.0))

    def test_parse_detail_empty_detail_returns_empty(self) -> None:
        entry = AuditEntry(
            id=uuid.uuid4(),
            action="trade.fill",
            actor="broker",
            resource="BTC/USD",
            detail="",
            timestamp=datetime.now(UTC),
            previous_hash="0" * 64,
            hash="a" * 64,
        )
        assert ReplayService._parse_detail(entry) == {}

    def test_parse_detail_non_json_returns_empty(self) -> None:
        entry = AuditEntry(
            id=uuid.uuid4(),
            action="trade.fill",
            actor="broker",
            resource="BTC/USD",
            detail="{not-json",
            timestamp=datetime.now(UTC),
            previous_hash="0" * 64,
            hash="a" * 64,
        )
        assert ReplayService._parse_detail(entry) == {}

    def test_multirestart_replay_drill_passes(self) -> None:
        """The committed G-05 drill must stay green — causal replay across
        simulated restarts is the standing proof of the exit test."""
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "evidence"
            / "run_multirestart_replay.py"
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
        assert "every cycle reconstructed (chain complete): True" in proc.stdout
        assert "audit chain valid after restarts: True" in proc.stdout
