import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from tests.test_repository_contracts import RepositoryContractTests
from traderos.domain.entities import OHLCV
from traderos.domain.entities import AssetClass
from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Candle
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import Indicator
from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.entities import Lesson
from traderos.domain.entities import LiquidityZone
from traderos.domain.entities import Market
from traderos.domain.entities import Metrics
from traderos.domain.entities import Observation
from traderos.domain.entities import Position
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.entities import Strategy
from traderos.domain.entities import Timeframe
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import ZoneType
from traderos.infrastructure.repositories.sqlite import SQLiteBacktestResultRepository
from traderos.infrastructure.repositories.sqlite import SQLiteCandleRepository
from traderos.infrastructure.repositories.sqlite import SQLiteExperimentRepository
from traderos.infrastructure.repositories.sqlite import SQLiteExperimentResultRepository
from traderos.infrastructure.repositories.sqlite import SQLiteHypothesisRepository
from traderos.infrastructure.repositories.sqlite import SQLiteIndicatorRepository
from traderos.infrastructure.repositories.sqlite import SQLiteKnowledgeEdgeRepository
from traderos.infrastructure.repositories.sqlite import SQLiteKnowledgeNodeRepository
from traderos.infrastructure.repositories.sqlite import SQLiteLessonRepository
from traderos.infrastructure.repositories.sqlite import SQLiteLiquidityZoneRepository
from traderos.infrastructure.repositories.sqlite import SQLiteMarketRepository
from traderos.infrastructure.repositories.sqlite import SQLiteObservationRepository
from traderos.infrastructure.repositories.sqlite import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite import SQLiteSignalRepository
from traderos.infrastructure.repositories.sqlite import SQLiteStrategyRepository
from traderos.infrastructure.repositories.sqlite import SQLiteTradeRepository


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _market() -> Market:
    return Market(symbol="BTCUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")


def _candle() -> Candle:
    return Candle(
        market_id=uuid.uuid4(),
        ohlcv=OHLCV(Decimal(100), Decimal(105), Decimal(99), Decimal(102), Decimal(1000)),
        timestamp=datetime.now(tz=UTC),
        timeframe=Timeframe.HOUR_1,
    )


def _indicator() -> Indicator:
    return Indicator(
        market_id=uuid.uuid4(),
        timestamp=datetime.now(tz=UTC),
        name="RSI",
        value=45.5,
    )


def _liquidity_zone() -> LiquidityZone:
    return LiquidityZone(
        market_id=uuid.uuid4(),
        price_level=50000.0,
        zone_type=ZoneType.SUPPORT,
        strength=3,
        detected_at=datetime.now(tz=UTC),
    )


def _signal() -> Signal:
    now = datetime.now(tz=UTC)
    return Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection.LONG,
        confidence=0.8,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )


def _strategy() -> Strategy:
    return Strategy(name="TestMA", params={"period": 20}, version="1.0.0")


def _backtest_result() -> BacktestResult:
    now = datetime.now(tz=UTC)
    return BacktestResult(
        strategy_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        metrics=Metrics(),
        equity_curve=EquityCurve(),
        period_start=now,
        period_end=now,
    )


def _trade() -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        side=TradeSide.BUY,
        quantity=1.0,
        price=50000.0,
    )


def _position() -> Position:
    return Position(
        market_id=uuid.uuid4(),
        quantity=10.0,
        entry_price=50000.0,
        current_price=51000.0,
        pnl=10000.0,
    )


def _observation() -> Observation:
    return Observation(
        timestamp=datetime.now(tz=UTC),
        symbol="BTCUSDT",
        content="Test",
        tags=[],
    )


def _hypothesis() -> Hypothesis:
    return Hypothesis(observation_id=uuid.uuid4(), content="Test hypothesis")


def _experiment() -> Experiment:
    return Experiment(hypothesis_id=uuid.uuid4(), params={"period": "90d"})


def _experiment_result() -> ExperimentResult:
    return ExperimentResult(experiment_id=uuid.uuid4(), metrics={"win_rate": 0.65})


def _lesson() -> Lesson:
    return Lesson(result_id=uuid.uuid4(), content="Test lesson", tags=["test"])


def _knowledge_node() -> KnowledgeNode:
    return KnowledgeNode(label="Test Node", node_type="concept", content="Test content")


def _knowledge_edge() -> KnowledgeEdge:
    return KnowledgeEdge(source_id=uuid.uuid4(), target_id=uuid.uuid4(), relationship="related")


class TestSQLiteMarketRepository(RepositoryContractTests[Market]):
    def make_repository(self):
        return SQLiteMarketRepository(_db())

    def make_entity(self):
        return _market()


class TestSQLiteCandleRepository(RepositoryContractTests[Candle]):
    def make_repository(self):
        return SQLiteCandleRepository(_db())

    def make_entity(self):
        return _candle()


class TestSQLiteIndicatorRepository(RepositoryContractTests[Indicator]):
    def make_repository(self):
        return SQLiteIndicatorRepository(_db())

    def make_entity(self):
        return _indicator()


class TestSQLiteLiquidityZoneRepository(RepositoryContractTests[LiquidityZone]):
    def make_repository(self):
        return SQLiteLiquidityZoneRepository(_db())

    def make_entity(self):
        return _liquidity_zone()


class TestSQLiteSignalRepository(RepositoryContractTests[Signal]):
    def make_repository(self):
        return SQLiteSignalRepository(_db())

    def make_entity(self):
        return _signal()


class TestSQLiteStrategyRepository(RepositoryContractTests[Strategy]):
    def make_repository(self):
        return SQLiteStrategyRepository(_db())

    def make_entity(self):
        return _strategy()

    def test_template_roundtrip(self) -> None:
        repo = self.make_repository()
        strategy = Strategy(
            name="TemplateMA",
            params={"period": 5},
            version="1.0.0",
            template="moving_average_trend",
        )
        repo.add(strategy)
        loaded = repo.get(strategy.id)
        assert loaded is not None
        assert loaded.template == "moving_average_trend"
        assert loaded.params == {"period": 5}

    def test_template_column_self_heals_on_legacy_table(self) -> None:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE strategies ("
            " id TEXT PRIMARY KEY, name TEXT UNIQUE, params TEXT, version TEXT,"
            " status TEXT, created_at TEXT)"
        )
        conn.commit()
        repo = SQLiteStrategyRepository(conn)
        repo.add(Strategy(name="Healed", params={}, version="1.0.0", template="mean_reversion"))
        loaded = repo.get_by_name("Healed")
        assert loaded is not None
        assert loaded.template == "mean_reversion"
        conn.close()


class TestSQLiteBacktestResultRepository(RepositoryContractTests[BacktestResult]):
    def make_repository(self):
        return SQLiteBacktestResultRepository(_db())

    def make_entity(self):
        return _backtest_result()


class TestSQLiteTradeRepository(RepositoryContractTests[Trade]):
    def make_repository(self):
        return SQLiteTradeRepository(_db())

    def make_entity(self):
        return _trade()


class TestSQLitePositionRepository(RepositoryContractTests[Position]):
    def make_repository(self):
        return SQLitePositionRepository(_db())

    def make_entity(self):
        return _position()


class TestSQLiteObservationRepository(RepositoryContractTests[Observation]):
    def make_repository(self):
        return SQLiteObservationRepository(_db())

    def make_entity(self):
        return _observation()


class TestSQLiteHypothesisRepository(RepositoryContractTests[Hypothesis]):
    def make_repository(self):
        return SQLiteHypothesisRepository(_db())

    def make_entity(self):
        return _hypothesis()


class TestSQLiteExperimentRepository(RepositoryContractTests[Experiment]):
    def make_repository(self):
        return SQLiteExperimentRepository(_db())

    def make_entity(self):
        return _experiment()


class TestSQLiteExperimentResultRepository(RepositoryContractTests[ExperimentResult]):
    def make_repository(self):
        return SQLiteExperimentResultRepository(_db())

    def make_entity(self):
        return _experiment_result()


class TestSQLiteLessonRepository(RepositoryContractTests[Lesson]):
    def make_repository(self):
        return SQLiteLessonRepository(_db())

    def make_entity(self):
        return _lesson()


class TestSQLiteKnowledgeNodeRepository(RepositoryContractTests[KnowledgeNode]):
    def make_repository(self):
        return SQLiteKnowledgeNodeRepository(_db())

    def make_entity(self):
        return _knowledge_node()


class TestSQLiteKnowledgeEdgeRepository(RepositoryContractTests[KnowledgeEdge]):
    def make_repository(self):
        return SQLiteKnowledgeEdgeRepository(_db())

    def make_entity(self):
        return _knowledge_edge()
