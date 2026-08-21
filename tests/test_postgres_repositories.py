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
from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import HypothesisStatus
from traderos.domain.entities import Lesson
from traderos.domain.entities import Metrics
from traderos.domain.entities import Observation
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
from traderos.infrastructure.repositories.postgres.research import PostgresExperimentRepository
from traderos.infrastructure.repositories.postgres.research import (
    PostgresExperimentResultRepository,
)
from traderos.infrastructure.repositories.postgres.research import PostgresHypothesisRepository
from traderos.infrastructure.repositories.postgres.research import PostgresLessonRepository
from traderos.infrastructure.repositories.postgres.research import PostgresObservationRepository
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
    "user_api_keys",
    "user_sessions",
    "users",
    "observations",
    "hypotheses",
    "experiments",
    "experiment_results",
    "lessons",
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

    def test_abstract_surface_raises_not_implemented(self, pg_conn) -> None:
        class Partial(PostgresRepository):
            def _create_table(self) -> None:
                pass

        repo = Partial(pg_conn)
        with pytest.raises(NotImplementedError):
            _ = repo._table_name
        with pytest.raises(NotImplementedError):
            _ = repo._columns
        with pytest.raises(NotImplementedError):
            repo._to_row(object())
        with pytest.raises(NotImplementedError):
            repo._from_row(None)


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


class TestPostgresUserRepository:
    def test_list_users_orders_by_created_at(self, pg_conn) -> None:
        from traderos.domain.entities.user import User
        from traderos.domain.entities.user import UserRole
        from traderos.domain.entities.user import UserStatus
        from traderos.infrastructure.repositories.postgres.users import PostgresUserRepository

        repo = PostgresUserRepository(pg_conn)
        assert repo.list_users() == []
        earlier = User(
            id=uuid.uuid4(),
            username="u-list-a",
            password_hash="h",
            role=UserRole.OPERATOR,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        later = User(
            id=uuid.uuid4(),
            username="u-list-b",
            password_hash="h",
            role=UserRole.VIEWER,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        repo.create_user(earlier)
        repo.create_user(later)
        assert [u.username for u in repo.list_users()] == ["u-list-a", "u-list-b"]

    def test_user_crud_and_role_roundtrip(self, pg_conn) -> None:
        from traderos.domain.entities.user import User
        from traderos.domain.entities.user import UserRole
        from traderos.domain.entities.user import UserStatus
        from traderos.infrastructure.repositories.postgres.users import PostgresUserRepository

        repo = PostgresUserRepository(pg_conn)
        user = User(
            id=uuid.uuid4(),
            username="ops-admin",
            password_hash="pbkdf2_sha256$100000$salt$digest",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        repo.create_user(user)
        loaded = repo.get_user(user.id)
        assert loaded is not None
        assert loaded.username == "ops-admin"
        assert loaded.role == UserRole.ADMIN
        assert loaded.status == UserStatus.ACTIVE
        by_name = repo.get_user_by_username("ops-admin")
        assert by_name is not None and by_name.id == user.id
        assert repo.get_user_by_username("missing") is None

    def test_session_create_fetch_delete(self, pg_conn) -> None:
        from traderos.domain.entities.user import User
        from traderos.domain.entities.user import UserRole
        from traderos.domain.entities.user import UserSession
        from traderos.domain.entities.user import UserStatus
        from traderos.infrastructure.repositories.postgres.users import PostgresUserRepository

        repo = PostgresUserRepository(pg_conn)
        user = User(
            id=uuid.uuid4(),
            username="ops-operator",
            password_hash="h",
            role=UserRole.OPERATOR,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        repo.create_user(user)
        session = UserSession(
            token_hash="abc123hash",
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            created_at=datetime.now(UTC),
        )
        repo.create_session(session)
        fetched = repo.get_session("abc123hash")
        assert fetched is not None
        assert fetched.user_id == user.id
        repo.delete_session("abc123hash")
        assert repo.get_session("abc123hash") is None

    def test_api_key_crud_and_revocation(self, pg_conn) -> None:
        from traderos.domain.entities.user import User
        from traderos.domain.entities.user import UserApiKey
        from traderos.domain.entities.user import UserRole
        from traderos.domain.entities.user import UserStatus
        from traderos.infrastructure.repositories.postgres.users import PostgresUserRepository

        repo = PostgresUserRepository(pg_conn)
        user = User(
            id=uuid.uuid4(),
            username="uk-user",
            password_hash="h",
            role=UserRole.VIEWER,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        repo.create_user(user)
        key = UserApiKey(
            id=uuid.uuid4(),
            user_id=user.id,
            label="instrument",
            key_hash="key-hash-1",
            prefix="trd_x",
            created_at=datetime.now(UTC),
        )
        repo.create_api_key(key)
        fetched = repo.get_api_key("key-hash-1")
        assert fetched is not None and fetched.label == "instrument"
        listed = repo.list_api_keys(user.id)
        assert len(listed) == 1
        repo.revoke_api_key(key.id)
        revoked = repo.get_api_key("key-hash-1")
        assert revoked is not None and revoked.revoked_at is not None


class TestPostgresResearchRepositories:
    def _make_observation(self, **kw) -> Observation:
        fields = {
            "timestamp": datetime.now(UTC),
            "symbol": "BTCUSDT",
            "content": "volume spike observed",
            "tags": ["volume", "crypto"],
        }
        fields.update(kw)
        return Observation(**fields)

    def test_observation_roundtrip_and_get_by_symbol(self, pg_conn) -> None:
        repo = PostgresObservationRepository(pg_conn)
        obs = self._make_observation()
        repo.add(obs)
        fetched = repo.get(obs.id)
        assert fetched is not None
        assert fetched.symbol == "BTCUSDT"
        assert fetched.tags == ["volume", "crypto"]
        by_symbol = repo.get_by_symbol("BTCUSDT")
        assert any(o.id == obs.id for o in by_symbol)
        assert repo.get_by_symbol("NOPE") == []

    def test_observation_delete(self, pg_conn) -> None:
        repo = PostgresObservationRepository(pg_conn)
        obs = self._make_observation()
        repo.add(obs)
        repo.delete(obs.id)
        assert repo.get(obs.id) is None

    def test_hypothesis_roundtrip_and_get_by_observation(self, pg_conn) -> None:
        obs = self._make_observation()
        PostgresObservationRepository(pg_conn).add(obs)
        repo = PostgresHypothesisRepository(pg_conn)
        hyp = Hypothesis(observation_id=obs.id, content="volume precedes breakout")
        repo.add(hyp)
        fetched = repo.get(hyp.id)
        assert fetched is not None
        assert fetched.status == HypothesisStatus.PROPOSED
        assert fetched.observation_id == obs.id
        by_obs = repo.get_by_observation(obs.id)
        assert any(h.id == hyp.id for h in by_obs)

    def test_experiment_roundtrip_and_get_by_hypothesis(self, pg_conn) -> None:
        obs = self._make_observation()
        PostgresObservationRepository(pg_conn).add(obs)
        hyp = Hypothesis(observation_id=obs.id, content="test")
        PostgresHypothesisRepository(pg_conn).add(hyp)
        repo = PostgresExperimentRepository(pg_conn)
        exp = Experiment(hypothesis_id=hyp.id, params={"window": 20}, results={"sharpe": 1.2})
        repo.add(exp)
        fetched = repo.get(exp.id)
        assert fetched is not None
        assert fetched.params == {"window": 20}
        assert fetched.results == {"sharpe": 1.2}
        by_hyp = repo.get_by_hypothesis(hyp.id)
        assert any(e.id == exp.id for e in by_hyp)

    def test_experiment_result_roundtrip_and_get_by_experiment(self, pg_conn) -> None:
        obs = self._make_observation()
        PostgresObservationRepository(pg_conn).add(obs)
        hyp = Hypothesis(observation_id=obs.id, content="test")
        PostgresHypothesisRepository(pg_conn).add(hyp)
        exp = Experiment(hypothesis_id=hyp.id, params={})
        PostgresExperimentRepository(pg_conn).add(exp)
        repo = PostgresExperimentResultRepository(pg_conn)
        res = ExperimentResult(experiment_id=exp.id, metrics={"pnl": 5.0}, visual_path="/tmp/x.png")
        repo.add(res)
        fetched = repo.get(res.id)
        assert fetched is not None
        assert fetched.metrics == {"pnl": 5.0}
        assert fetched.visual_path == "/tmp/x.png"
        by_exp = repo.get_by_experiment(exp.id)
        assert any(r.id == res.id for r in by_exp)

    def test_lesson_roundtrip_get_by_result_and_tags(self, pg_conn) -> None:
        obs = self._make_observation()
        PostgresObservationRepository(pg_conn).add(obs)
        hyp = Hypothesis(observation_id=obs.id, content="test")
        PostgresHypothesisRepository(pg_conn).add(hyp)
        exp = Experiment(hypothesis_id=hyp.id, params={})
        PostgresExperimentRepository(pg_conn).add(exp)
        res = ExperimentResult(experiment_id=exp.id, metrics={})
        PostgresExperimentResultRepository(pg_conn).add(res)
        repo = PostgresLessonRepository(pg_conn)
        lesson = Lesson(result_id=res.id, content="lesson", tags=["risk"])
        repo.add(lesson)
        fetched = repo.get(lesson.id)
        assert fetched is not None
        assert fetched.tags == ["risk"]
        assert any(item.id == lesson.id for item in repo.get_by_result(res.id))
        assert any(item.id == lesson.id for item in repo.get_by_tags(["risk"]))
