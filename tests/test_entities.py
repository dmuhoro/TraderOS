import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.domain.entities import OHLCV
from traderos.domain.entities import AssetClass
from traderos.domain.entities import BacktestResult
from traderos.domain.entities import Candle
from traderos.domain.entities import EquityCurve
from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import HypothesisStatus
from traderos.domain.entities import Indicator
from traderos.domain.entities import KnowledgeEdge
from traderos.domain.entities import KnowledgeNode
from traderos.domain.entities import Lesson
from traderos.domain.entities import LiquidityZone
from traderos.domain.entities import Market
from traderos.domain.entities import MarketStatus
from traderos.domain.entities import Metrics
from traderos.domain.entities import Observation
from traderos.domain.entities import Position
from traderos.domain.entities import SessionConfig
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.entities import Strategy
from traderos.domain.entities import StrategyStatus
from traderos.domain.entities import Timeframe
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.entities import TradeStatus
from traderos.domain.entities import ZoneType


def _utcnow():
    return datetime.now(tz=UTC)


def _ohlcv_args():
    return (Decimal(100), Decimal(105), Decimal(99), Decimal(102), Decimal(1000))


class TestValueObjects:
    def test_ohlcv_validation_passes(self) -> None:
        ohlcv = OHLCV(*_ohlcv_args())
        ohlcv.validate()

    def test_ohlcv_low_exceeds_high_raises(self) -> None:
        ohlcv = OHLCV(Decimal(100), Decimal(99), Decimal(101), Decimal(102), Decimal(1000))
        with pytest.raises(ValueError):
            ohlcv.validate()

    def test_ohlcv_negative_volume_raises(self) -> None:
        ohlcv = OHLCV(Decimal(100), Decimal(105), Decimal(99), Decimal(102), Decimal(-1))
        with pytest.raises(ValueError):
            ohlcv.validate()

    def test_ohlcv_negative_price_raises(self) -> None:
        ohlcv = OHLCV(Decimal(-1), Decimal(105), Decimal(99), Decimal(102), Decimal(1000))
        with pytest.raises(ValueError):
            ohlcv.validate()

    def test_metrics_defaults(self) -> None:
        m = Metrics()
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0

    def test_session_config_frozen(self) -> None:
        config = SessionConfig(name="London", start_hour=8, end_hour=16)
        assert config.name == "London"
        assert config.start_hour == 8
        assert config.end_hour == 16
        assert isinstance(config.session_id, uuid.UUID)

    def test_equity_curve_empty(self) -> None:
        ec = EquityCurve()
        assert len(ec.points) == 0


class TestMarket:
    def test_default_status(self) -> None:
        market = Market(symbol="BTCUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")
        assert market.status == MarketStatus.ACTIVE
        assert isinstance(market.id, uuid.UUID)

    def test_frozen(self) -> None:
        market = Market(symbol="BTCUSDT", asset_class=AssetClass.CRYPTO, exchange="BINANCE")
        with pytest.raises(AttributeError):
            market.symbol = "ETHUSDT"


class TestCandle:
    def test_valid_candle(self) -> None:
        market_id = uuid.uuid4()
        ohlcv = OHLCV(*_ohlcv_args())
        candle = Candle(
            market_id=market_id,
            ohlcv=ohlcv,
            timestamp=_utcnow(),
            timeframe=Timeframe.HOUR_1,
        )
        assert candle.market_id == market_id

    def test_invalid_candle_raises(self) -> None:
        ohlcv = OHLCV(Decimal(100), Decimal(99), Decimal(101), Decimal(102), Decimal(1000))
        with pytest.raises(ValueError):
            Candle(
                market_id=uuid.uuid4(),
                ohlcv=ohlcv,
                timestamp=_utcnow(),
                timeframe=Timeframe.HOUR_1,
            )


class TestSignal:
    def test_valid_signal(self) -> None:
        now = _utcnow()
        signal = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.8,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        assert signal.direction == SignalDirection.LONG

    def test_confidence_out_of_range_raises(self) -> None:
        now = _utcnow()
        with pytest.raises(ValueError):
            Signal(
                market_id=uuid.uuid4(),
                strategy_id=uuid.uuid4(),
                direction=SignalDirection.LONG,
                confidence=1.5,
                generated_at=now,
                expires_at=now + timedelta(hours=1),
            )

    def test_expires_before_generated_raises(self) -> None:
        now = _utcnow()
        with pytest.raises(ValueError):
            Signal(
                market_id=uuid.uuid4(),
                strategy_id=uuid.uuid4(),
                direction=SignalDirection.LONG,
                confidence=0.5,
                generated_at=now,
                expires_at=now - timedelta(hours=1),
            )


class TestStrategy:
    def test_default_status(self) -> None:
        strategy = Strategy(name="TestMA", params={"period": 20}, version="1.0.0")
        assert strategy.status == StrategyStatus.DRAFT

    def test_frozen(self) -> None:
        strategy = Strategy(name="Test", params={}, version="1.0.0")
        with pytest.raises(AttributeError):
            strategy.name = "NewName"


class TestBacktestResult:
    def test_create(self) -> None:
        now = _utcnow()
        result = BacktestResult(
            strategy_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            metrics=Metrics(total_return=0.15, sharpe_ratio=1.5),
            equity_curve=EquityCurve(),
            period_start=now,
            period_end=now + timedelta(days=30),
        )
        assert result.metrics.total_return == 0.15


class TestTrade:
    def test_default_status(self) -> None:
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=1.0,
            price=50000.0,
        )
        assert trade.status == TradeStatus.PENDING

    def test_fill_transition(self) -> None:
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=1.0,
            price=50000.0,
        )
        assert trade.status == TradeStatus.PENDING
        trade.submit("ext-1")
        trade.fill(1.0, 50100.0)
        assert trade.status == TradeStatus.FILLED
        assert trade.filled_price == 50100.0
        assert trade.filled_quantity == 1.0

    def test_cancel_transition(self) -> None:
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=1.0,
            price=50000.0,
        )
        trade.cancel()
        assert trade.status == TradeStatus.CANCELLED

    def test_reject_transition(self) -> None:
        trade = Trade(
            signal_id=uuid.uuid4(),
            market_id=uuid.uuid4(),
            side=TradeSide.BUY,
            quantity=1.0,
            price=50000.0,
        )
        trade.reject()
        assert trade.status == TradeStatus.REJECTED


class TestPosition:
    def test_create(self) -> None:
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=50000.0,
            current_price=51000.0,
            pnl=10000.0,
        )
        assert pos.pnl == 10000.0


class TestResearch:
    def test_observation_creation(self) -> None:
        obs = Observation(
            timestamp=_utcnow(),
            symbol="BTCUSDT",
            content="Price rejected at 70k",
            tags=["liquidity", "resistance"],
        )
        assert "resistance" in obs.tags
        assert isinstance(obs.id, uuid.UUID)

    def test_hypothesis_workflow(self) -> None:
        obs = Observation(timestamp=_utcnow(), symbol="BTCUSDT", content="Test", tags=[])
        hyp = Hypothesis(observation_id=obs.id, content="70k is supply zone")
        assert hyp.status == HypothesisStatus.PROPOSED

    def test_experiment_with_results(self) -> None:
        hyp = Hypothesis(observation_id=uuid.uuid4(), content="Test")
        exp = Experiment(hypothesis_id=hyp.id, params={"period": "90d"})
        result = ExperimentResult(experiment_id=exp.id, metrics={"win_rate": 0.65})
        lesson = Lesson(result_id=result.id, content="Wait for sweep", tags=["liquidity"])
        assert lesson.content == "Wait for sweep"

    def test_research_chain_completeness(self) -> None:
        obs = Observation(timestamp=_utcnow(), symbol="TEST", content="a", tags=[])
        hyp = Hypothesis(observation_id=obs.id, content="b")
        exp = Experiment(hypothesis_id=hyp.id, params={})
        res = ExperimentResult(experiment_id=exp.id, metrics={})
        les = Lesson(result_id=res.id, content="c", tags=[])
        assert les.result_id == res.id
        assert res.experiment_id == exp.id
        assert exp.hypothesis_id == hyp.id
        assert hyp.observation_id == obs.id


class TestKnowledge:
    def test_knowledge_node(self) -> None:
        node = KnowledgeNode(
            label="RSI Divergence",
            node_type="pattern",
            content="RSI divergence observed",
        )
        assert node.node_type == "pattern"

    def test_knowledge_edge(self) -> None:
        src = uuid.uuid4()
        tgt = uuid.uuid4()
        edge = KnowledgeEdge(source_id=src, target_id=tgt, relationship="correlated_to")
        assert edge.source_id == src
        assert edge.target_id == tgt

    def test_knowledge_node_with_embedding(self) -> None:
        node = KnowledgeNode(
            label="Test", node_type="concept", content="test", embedding=[0.1, 0.2, 0.3]
        )
        assert node.embedding == [0.1, 0.2, 0.3]


class TestLiquidity:
    def test_liquidity_zone(self) -> None:
        zone = LiquidityZone(
            market_id=uuid.uuid4(),
            price_level=50000.0,
            zone_type=ZoneType.SUPPORT,
            strength=3,
            detected_at=_utcnow(),
        )
        assert zone.zone_type == ZoneType.SUPPORT
        assert zone.strength == 3


class TestIndicator:
    def test_indicator_creation(self) -> None:
        ind = Indicator(
            market_id=uuid.uuid4(),
            timestamp=_utcnow(),
            name="RSI",
            value=45.5,
        )
        assert ind.name == "RSI"
        assert ind.value == 45.5
