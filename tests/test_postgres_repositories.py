from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import psycopg2
import pytest

from traderos.domain.entities import BacktestResult
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Metrics
from traderos.domain.entities import Position
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.entities import Strategy
from traderos.domain.entities import StrategyStatus
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import from_json
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_json
from traderos.infrastructure.repositories.postgres.base import to_uuid
from traderos.infrastructure.repositories.postgres.signals import PostgresSignalRepository
from traderos.infrastructure.repositories.postgres.strategies import (
    PostgresBacktestResultRepository,
)
from traderos.infrastructure.repositories.postgres.strategies import PostgresStrategyRepository
from traderos.infrastructure.repositories.postgres.trades import PostgresPositionRepository
from traderos.infrastructure.repositories.postgres.trades import PostgresTradeRepository
from traderos.infrastructure.repositories.postgres.workflows import (
    PostgresOperatorWorkflowRepository,
)

DSN = os.environ.get(
    "POSTGRES_TEST_DSN",
    "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
)


def _pg_reachable(dsn: str, timeout: int = 3) -> bool:
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        conn.close()
        return True
    except psycopg2.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(DSN),
    reason=f"Postgres not reachable at {DSN} — skipped, not passed",
)

_REPO_TABLES = (
    "signals",
    "trades",
    "positions",
    "strategies",
    "backtest_results",
    "operator_workflow",
    "workflow_transitions",
)


@pytest.fixture
def pg_conn():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    yield conn
    with conn.cursor() as cur:
        for table in _REPO_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.close()


class TestBaseHelpers:
    def test_to_uuid_accepts_string_and_uuid(self) -> None:
        value = uuid.uuid4()
        assert to_uuid(str(value)) == value
        assert to_uuid(value) is value

    def test_to_dt_accepts_iso_and_datetime(self) -> None:
        now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
        assert to_dt(now.isoformat()) == now
        assert to_dt(now) is now

    def test_to_json_serializes_with_default(self) -> None:
        assert to_json({"n": 1}) == '{"n": 1}'
        assert '"2026-' in to_json({"at": datetime(2026, 1, 1, tzinfo=UTC)})

    def test_from_json_parses_and_handles_none(self) -> None:
        assert from_json('{"a": 1}') == {"a": 1}
        assert from_json(None) is None

    def test_postgres_repository_base_requires_table_name(self, pg_conn) -> None:
        with pytest.raises(NotImplementedError):

            class Broken(PostgresRepository):
                pass

            Broken(pg_conn)


class TestPostgresSignalRepository:
    def _make_signal(self, direction: SignalDirection = SignalDirection.LONG, **kw) -> Signal:
        now = datetime.now(UTC)
        fields = {
            "market_id": uuid.uuid4(),
            "strategy_id": uuid.uuid4(),
            "direction": direction,
            "confidence": 0.8,
            "generated_at": now,
            "expires_at": now + timedelta(hours=1),
        }
        fields.update(kw)
        return Signal(**fields)

    def test_add_get_list_roundtrip(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        signal = self._make_signal()
        repo.add(signal)
        fetched = repo.get(signal.id)
        assert fetched is not None
        assert fetched.direction == signal.direction
        assert fetched.confidence == signal.confidence
        assert fetched.generated_at == signal.generated_at
        assert repo.list() == [signal]

    def test_add_returns_deepcopy(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        signal = self._make_signal()
        returned = repo.add(signal)
        assert returned == signal
        assert returned is not signal

    def test_get_missing_returns_none(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        assert repo.get(uuid.uuid4()) is None

    def test_update_persists_changes(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        signal = self._make_signal(confidence=0.3)
        repo.add(signal)
        updated = replace(signal, confidence=0.9)
        repo.update(updated)
        assert repo.get(signal.id).confidence == 0.9

    def test_delete_removes_entity(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        signal = self._make_signal()
        repo.add(signal)
        repo.delete(signal.id)
        assert repo.get(signal.id) is None
        assert repo.list() == []

    def test_get_active_excludes_expired(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        now = datetime.now(UTC)
        active = self._make_signal(expires_at=now + timedelta(hours=1))
        expired = self._make_signal(
            generated_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        repo.add(active)
        repo.add(expired)
        result = repo.get_active(active.market_id)
        assert [s.id for s in result] == [active.id]

    def test_get_active_empty_market(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        assert repo.get_active(uuid.uuid4()) == []

    def test_get_by_strategy(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        strategy = uuid.uuid4()
        a = self._make_signal(strategy_id=strategy)
        b = self._make_signal()
        other = self._make_signal()
        repo.add(a)
        repo.add(b)
        repo.add(other)
        ids = [s.id for s in repo.get_by_strategy(strategy)]
        assert a.id in ids
        assert b.id not in ids

    def test_get_range_filters_by_generated_at(self, pg_conn) -> None:
        repo = PostgresSignalRepository(pg_conn)
        now = datetime.now(UTC)
        inside = self._make_signal(generated_at=now)
        outside = self._make_signal(
            generated_at=now + timedelta(days=3),
            expires_at=now + timedelta(days=3, hours=1),
        )
        repo.add(inside)
        repo.add(outside)
        result = repo.get_range(
            inside.market_id, now - timedelta(hours=1), now + timedelta(hours=1)
        )
        assert [s.id for s in result] == [inside.id]


class TestPostgresTradeRepository:
    def _make_trade(self, status: TradeStatus = TradeStatus.PENDING) -> Trade:
        return Trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=0.1,
            price=50000.0,
            status=status,
        )

    def test_add_get_list_roundtrip(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        trade = self._make_trade()
        repo.add(trade)
        fetched = repo.get(trade.id)
        assert fetched is not None
        assert fetched.side == trade.side
        assert fetched.status == trade.status
        assert fetched.quantity == trade.quantity
        assert repo.list() == [trade]

    def test_filled_trade_roundtrip_preserves_fill_fields(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        trade = self._make_trade()
        trade.submit("ext-123")
        trade.fill(0.1, 50100.0)
        repo.add(trade)
        fetched = repo.get(trade.id)
        assert fetched.status == TradeStatus.FILLED
        assert fetched.filled_quantity == 0.1
        assert fetched.filled_price == 50100.0
        assert fetched.filled_at is not None

    def test_update_after_submit(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        trade = self._make_trade()
        repo.add(trade)
        trade.submit("ext-123")
        repo.update(trade)
        fetched = repo.get(trade.id)
        assert fetched.status == TradeStatus.SUBMITTED
        assert fetched.external_order_id == "ext-123"

    def test_get_open_excludes_terminal_states(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        pending = self._make_trade()
        filled = self._make_trade(status=TradeStatus.FILLED)
        cancelled = self._make_trade(status=TradeStatus.CANCELLED)
        repo.add(pending)
        repo.add(filled)
        repo.add(cancelled)
        open_ids = {t.id for t in repo.get_open()}
        assert pending.id in open_ids
        assert filled.id not in open_ids
        assert cancelled.id not in open_ids

    def test_get_open_includes_acknowledged(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        trade = self._make_trade(status=TradeStatus.ACKNOWLEDGED)
        repo.add(trade)
        assert [t.id for t in repo.get_open()] == [trade.id]

    def test_get_by_signal_and_market(self, pg_conn) -> None:
        repo = PostgresTradeRepository(pg_conn)
        signal = uuid.uuid4()
        market = uuid.uuid4()
        a = self._make_trade()
        b = self._make_trade()
        a.signal_id = signal
        a.market_id = market
        repo.add(a)
        repo.add(b)
        assert [t.id for t in repo.get_by_signal(signal)] == [a.id]
        assert [t.id for t in repo.get_by_market(market)] == [a.id]


class TestPostgresPositionRepository:
    def _make_position(self) -> Position:
        return Position(
            market_id=uuid.uuid4(),
            quantity=1.0,
            entry_price=100.0,
            current_price=105.0,
            pnl=5.0,
        )

    def test_add_get_list_roundtrip(self, pg_conn) -> None:
        repo = PostgresPositionRepository(pg_conn)
        pos = self._make_position()
        repo.add(pos)
        fetched = repo.get(pos.id)
        assert fetched is not None
        assert fetched.pnl == 5.0
        assert repo.list() == [pos]

    def test_get_by_market(self, pg_conn) -> None:
        repo = PostgresPositionRepository(pg_conn)
        pos = self._make_position()
        repo.add(pos)
        fetched = repo.get_by_market(pos.market_id)
        assert fetched is not None
        assert fetched.id == pos.id
        assert repo.get_by_market(uuid.uuid4()) is None

    def test_list_open_excludes_flat_positions(self, pg_conn) -> None:
        repo = PostgresPositionRepository(pg_conn)
        open_pos = self._make_position()
        flat = self._make_position()
        flat.quantity = 0.0
        flat.close(110.0)
        repo.add(open_pos)
        repo.add(flat)
        ids = [p.id for p in repo.list_open()]
        assert open_pos.id in ids
        assert flat.id not in ids

    def test_update_reflects_price_change(self, pg_conn) -> None:
        repo = PostgresPositionRepository(pg_conn)
        pos = self._make_position()
        repo.add(pos)
        pos.update_price(110.0)
        repo.update(pos)
        fetched = repo.get(pos.id)
        assert fetched.current_price == 110.0
        assert fetched.pnl == 10.0

    def test_delete(self, pg_conn) -> None:
        repo = PostgresPositionRepository(pg_conn)
        pos = self._make_position()
        repo.add(pos)
        repo.delete(pos.id)
        assert repo.get(pos.id) is None


class TestPostgresStrategyRepository:
    def _make_strategy(self, status: StrategyStatus = StrategyStatus.DRAFT) -> Strategy:
        return Strategy(
            name="momentum_1h",
            params={"trend_threshold": 0.02},
            version="1.0.0",
            status=status,
        )

    def test_add_get_list_roundtrip(self, pg_conn) -> None:
        repo = PostgresStrategyRepository(pg_conn)
        strategy = self._make_strategy()
        repo.add(strategy)
        fetched = repo.get(strategy.id)
        assert fetched is not None
        assert fetched.name == "momentum_1h"
        assert fetched.params == {"trend_threshold": 0.02}
        assert repo.list() == [strategy]

    def test_get_by_name(self, pg_conn) -> None:
        repo = PostgresStrategyRepository(pg_conn)
        strategy = self._make_strategy()
        repo.add(strategy)
        fetched = repo.get_by_name("momentum_1h")
        assert fetched is not None
        assert fetched.id == strategy.id
        assert repo.get_by_name("nope") is None

    def test_list_active_only_active_or_promoted(self, pg_conn) -> None:
        repo = PostgresStrategyRepository(pg_conn)
        draft = Strategy(
            name="momentum_1h_draft",
            params={},
            version="1.0.0",
            status=StrategyStatus.DRAFT,
        )
        active = Strategy(
            name="momentum_1h_active",
            params={},
            version="1.0.0",
            status=StrategyStatus.ACTIVE,
        )
        repo.add(draft)
        repo.add(active)
        ids = [s.id for s in repo.list_active()]
        assert active.id in ids
        assert draft.id not in ids

    def test_update_persists_params_and_status(self, pg_conn) -> None:
        repo = PostgresStrategyRepository(pg_conn)
        strategy = self._make_strategy()
        repo.add(strategy)
        updated = replace(strategy, params={"trend_threshold": 0.05}, status=StrategyStatus.ACTIVE)
        repo.update(updated)
        fetched = repo.get(strategy.id)
        assert fetched.params == {"trend_threshold": 0.05}
        assert fetched.status == StrategyStatus.ACTIVE

    def test_delete_strategy(self, pg_conn) -> None:
        repo = PostgresStrategyRepository(pg_conn)
        strategy = self._make_strategy()
        repo.add(strategy)
        repo.delete(strategy.id)
        assert repo.get(strategy.id) is None


class TestPostgresBacktestResultRepository:
    def _make_result(self) -> BacktestResult:
        return BacktestResult(
            strategy_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            metrics=Metrics(
                total_return=0.1,
                sharpe_ratio=1.2,
                total_trades=3,
            ),
            equity_curve=EquityCurve(
                points=(
                    (datetime.now(UTC), 100.0),
                    (datetime.now(UTC), 105.0),
                )
            ),
            period_start=datetime(2026, 1, 1, tzinfo=UTC),
            period_end=datetime(2026, 1, 31, tzinfo=UTC),
        )

    def test_add_get_list_roundtrip(self, pg_conn) -> None:
        repo = PostgresBacktestResultRepository(pg_conn)
        result = self._make_result()
        repo.add(result)
        fetched = repo.get(result.id)
        assert fetched is not None
        assert fetched.metrics.total_return == 0.1
        assert fetched.equity_curve.points[0][1] == 100.0
        assert repo.list() == [result]

    def test_get_by_strategy(self, pg_conn) -> None:
        repo = PostgresBacktestResultRepository(pg_conn)
        strategy = uuid.uuid4()
        a = replace(self._make_result(), strategy_id=strategy)
        b = self._make_result()
        repo.add(a)
        repo.add(b)
        ids = [r.id for r in repo.get_by_strategy(strategy)]
        assert a.id in ids
        assert b.id not in ids

    def test_get_by_market(self, pg_conn) -> None:
        repo = PostgresBacktestResultRepository(pg_conn)
        market = uuid.uuid4()
        a = replace(self._make_result(), market_id=market)
        repo.add(a)
        assert [r.id for r in repo.get_by_market(market)] == [a.id]


class TestPostgresOperatorWorkflowRepository:
    def test_save_load_roundtrip(self, pg_conn) -> None:
        from traderos.domain.services.operator_workflow import OperatorStep
        from traderos.domain.services.operator_workflow import OperatorWorkflow

        repo = PostgresOperatorWorkflowRepository(pg_conn)
        assert repo.load() is None
        workflow = OperatorWorkflow(current_step=OperatorStep.BROKER_CHECK)
        workflow.transitions = []
        repo.save(workflow)
        reloaded = repo.load()
        assert reloaded is not None
        assert reloaded.current_step == OperatorStep.BROKER_CHECK

    def test_save_upsert_single_row(self, pg_conn) -> None:
        from traderos.domain.services.operator_workflow import OperatorWorkflow

        repo = PostgresOperatorWorkflowRepository(pg_conn)
        workflow = OperatorWorkflow()
        repo.save(workflow)
        repo.save(workflow)
        reloaded = repo.load()
        assert reloaded is not None

    def test_persists_transitions(self, pg_conn) -> None:
        from traderos.domain.services.operator_workflow import OperatorStep
        from traderos.domain.services.operator_workflow import OperatorWorkflow
        from traderos.domain.services.operator_workflow import WorkflowTransition

        repo = PostgresOperatorWorkflowRepository(pg_conn)
        workflow = OperatorWorkflow(
            current_step=OperatorStep.CONTROLLED_LIVE,
        )
        workflow.transitions = [
            WorkflowTransition(
                from_step=OperatorStep.BROKER_CHECK,
                to_step=OperatorStep.CONTROLLED_LIVE,
                actor="ops",
                result="ok",
                timestamp=datetime.now(UTC),
            )
        ]
        repo.save(workflow)
        reloaded = repo.load()
        assert reloaded is not None
        assert len(reloaded.transitions) == 1
        assert reloaded.transitions[0].to_step == OperatorStep.CONTROLLED_LIVE
