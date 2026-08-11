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
from traderos.infrastructure.repositories.in_memory import InMemoryBacktestResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryCandleRepository
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentRepository
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryHypothesisRepository
from traderos.infrastructure.repositories.in_memory import InMemoryIndicatorRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeEdgeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeNodeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryLessonRepository
from traderos.infrastructure.repositories.in_memory import InMemoryLiquidityZoneRepository
from traderos.infrastructure.repositories.in_memory import InMemoryMarketRepository
from traderos.infrastructure.repositories.in_memory import InMemoryObservationRepository
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemorySignalRepository
from traderos.infrastructure.repositories.in_memory import InMemoryStrategyRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository


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


class TestInMemoryMarketRepository(RepositoryContractTests[Market]):
    def make_repository(self):
        return InMemoryMarketRepository()

    def make_entity(self):
        return _market()


class TestInMemoryCandleRepository(RepositoryContractTests[Candle]):
    def make_repository(self):
        return InMemoryCandleRepository()

    def make_entity(self):
        return _candle()


class TestInMemoryIndicatorRepository(RepositoryContractTests[Indicator]):
    def make_repository(self):
        return InMemoryIndicatorRepository()

    def make_entity(self):
        return _indicator()

    def test_get_by_name_filters_market_and_name(self) -> None:
        repo = InMemoryIndicatorRepository()
        market = uuid.uuid4()
        other_market = uuid.uuid4()
        a = Indicator(market_id=market, name="RSI", value=30.0, timestamp=datetime.now(tz=UTC))
        b = Indicator(market_id=market, name="MACD", value=5.0, timestamp=datetime.now(tz=UTC))
        c = Indicator(
            market_id=other_market,
            name="RSI",
            value=70.0,
            timestamp=datetime.now(tz=UTC),
        )
        repo.add(a)
        repo.add(b)
        repo.add(c)
        assert [i.id for i in repo.get_by_name(market, "RSI")] == [a.id]
        assert repo.get_by_name(other_market, "MACD") == []

    def test_get_latest_returns_most_recent(self) -> None:
        repo = InMemoryIndicatorRepository()
        market = uuid.uuid4()
        older = Indicator(
            market_id=market,
            name="RSI",
            value=40.0,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer = Indicator(
            market_id=market,
            name="RSI",
            value=55.0,
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
        )
        repo.add(older)
        repo.add(newer)
        assert repo.get_latest(market, "RSI").value == 55.0

    def test_get_latest_returns_none_when_absent(self) -> None:
        repo = InMemoryIndicatorRepository()
        assert repo.get_latest(uuid.uuid4(), "RSI") is None


class TestInMemoryLiquidityZoneRepository(RepositoryContractTests[LiquidityZone]):
    def make_repository(self):
        return InMemoryLiquidityZoneRepository()

    def make_entity(self):
        return _liquidity_zone()


class TestInMemorySignalRepository(RepositoryContractTests[Signal]):
    def make_repository(self):
        return InMemorySignalRepository()

    def make_entity(self):
        return _signal()


class TestInMemoryStrategyRepository(RepositoryContractTests[Strategy]):
    def make_repository(self):
        return InMemoryStrategyRepository()

    def make_entity(self):
        return _strategy()


class TestInMemoryBacktestResultRepository(RepositoryContractTests[BacktestResult]):
    def make_repository(self):
        return InMemoryBacktestResultRepository()

    def make_entity(self):
        return _backtest_result()


class TestInMemoryTradeRepository(RepositoryContractTests[Trade]):
    def make_repository(self):
        return InMemoryTradeRepository()

    def make_entity(self):
        return _trade()


class TestInMemoryPositionRepository(RepositoryContractTests[Position]):
    def make_repository(self):
        return InMemoryPositionRepository()

    def make_entity(self):
        return _position()


class TestInMemoryObservationRepository(RepositoryContractTests[Observation]):
    def make_repository(self):
        return InMemoryObservationRepository()

    def make_entity(self):
        return _observation()


class TestInMemoryHypothesisRepository(RepositoryContractTests[Hypothesis]):
    def make_repository(self):
        return InMemoryHypothesisRepository()

    def make_entity(self):
        return _hypothesis()


class TestInMemoryExperimentRepository(RepositoryContractTests[Experiment]):
    def make_repository(self):
        return InMemoryExperimentRepository()

    def make_entity(self):
        return _experiment()


class TestInMemoryExperimentResultRepository(RepositoryContractTests[ExperimentResult]):
    def make_repository(self):
        return InMemoryExperimentResultRepository()

    def make_entity(self):
        return _experiment_result()


class TestInMemoryLessonRepository(RepositoryContractTests[Lesson]):
    def make_repository(self):
        return InMemoryLessonRepository()

    def make_entity(self):
        return _lesson()


class TestInMemoryKnowledgeNodeRepository(RepositoryContractTests[KnowledgeNode]):
    def make_repository(self):
        return InMemoryKnowledgeNodeRepository()

    def make_entity(self):
        return _knowledge_node()


class TestInMemoryKnowledgeEdgeRepository(RepositoryContractTests[KnowledgeEdge]):
    def make_repository(self):
        return InMemoryKnowledgeEdgeRepository()

    def make_entity(self):
        return _knowledge_edge()


class TestInMemoryObservationFunctional:
    def test_get_by_symbol(self) -> None:
        repo = InMemoryObservationRepository()
        a = _observation()
        b = _observation()
        repo.add(a)
        repo.add(b)
        assert [o.id for o in repo.get_by_symbol(a.symbol)] == [a.id, b.id]
        assert repo.get_by_symbol("NOSUCH") == []


class TestInMemoryHypothesisFunctional:
    def test_get_by_observation(self) -> None:
        repo = InMemoryHypothesisRepository()
        a = _hypothesis()
        b = _hypothesis()
        repo.add(a)
        repo.add(b)
        assert [h.id for h in repo.get_by_observation(a.observation_id)] == [a.id]
        assert repo.get_by_observation(uuid.uuid4()) == []


class TestInMemoryExperimentFunctional:
    def test_get_by_hypothesis(self) -> None:
        repo = InMemoryExperimentRepository()
        a = _experiment()
        b = _experiment()
        repo.add(a)
        repo.add(b)
        assert [e.id for e in repo.get_by_hypothesis(a.hypothesis_id)] == [a.id]
        assert repo.get_by_hypothesis(uuid.uuid4()) == []


class TestInMemoryExperimentResultFunctional:
    def test_get_by_experiment(self) -> None:
        repo = InMemoryExperimentResultRepository()
        a = _experiment_result()
        b = _experiment_result()
        repo.add(a)
        repo.add(b)
        assert [r.id for r in repo.get_by_experiment(a.experiment_id)] == [a.id]
        assert repo.get_by_experiment(uuid.uuid4()) == []


class TestInMemoryLessonFunctional:
    def test_get_by_result(self) -> None:
        repo = InMemoryLessonRepository()
        a = _lesson()
        b = _lesson()
        repo.add(a)
        repo.add(b)
        assert [lesson.id for lesson in repo.get_by_result(a.result_id)] == [a.id]
        assert repo.get_by_result(uuid.uuid4()) == []

    def test_get_by_tags(self) -> None:
        repo = InMemoryLessonRepository()
        a = Lesson(result_id=uuid.uuid4(), content="one", tags=["alpha", "beta"])
        b = Lesson(result_id=uuid.uuid4(), content="two", tags=["gamma"])
        repo.add(a)
        repo.add(b)
        assert [lesson.id for lesson in repo.get_by_tags(["alpha"])] == [a.id]
        assert [lesson.id for lesson in repo.get_by_tags(["beta", "gamma"])] == [a.id, b.id]
        assert repo.get_by_tags(["omega"]) == []
