import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import MeanReversion
from traderos.domain.services.strategy_framework import MovingAverageTrend
from traderos.domain.services.strategy_framework import StrategyEvaluationService
from traderos.domain.services.strategy_framework import StrategyRegistry
from traderos.domain.services.strategy_framework import VolatilityBreakout
from traderos.domain.services.strategy_framework import registry


class TestStrategyBase:
    def test_sma_crossover_long(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"sma_20": 110.0, "sma_50": 100.0},
        )
        result = MovingAverageTrend().evaluate(state)
        assert result is not None
        assert result.direction == "long"
        assert result.confidence > 0

    def test_sma_crossover_short(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"sma_20": 90.0, "sma_50": 100.0},
        )
        result = MovingAverageTrend().evaluate(state)
        assert result is not None
        assert result.direction == "short"

    def test_no_signal_when_no_cross(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"sma_20": 101.0, "sma_50": 100.0},
        )
        result = MovingAverageTrend().evaluate(state)
        assert result is None

    def test_volatility_breakout(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"atr_14": 5.0, "close": 100.0, "sma_20": 95.0},
        )
        result = VolatilityBreakout().evaluate(state)
        assert result is not None
        # sma_20 < close → bearish → short
        assert result.direction == "short"

    def test_mean_reversion_short(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"bb_upper_20": 110.0, "bb_lower_20": 90.0, "close": 115.0},
        )
        result = MeanReversion().evaluate(state)
        assert result is not None
        assert result.direction == "short"

    def test_mean_reversion_long(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"bb_upper_20": 110.0, "bb_lower_20": 90.0, "close": 85.0},
        )
        result = MeanReversion().evaluate(state)
        assert result is not None
        assert result.direction == "long"

    def test_registry(self) -> None:
        r = StrategyRegistry()
        r.register(MovingAverageTrend)
        r.register(VolatilityBreakout)
        r.register(MeanReversion)
        names = r.list()
        assert "moving_average_trend" in names
        assert "volatility_breakout" in names
        assert "mean_reversion" in names

    def test_global_registry_has_all(self) -> None:
        names = registry.list()
        assert "moving_average_trend" in names

    def test_volatility_breakout_missing_indicators(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"close": 100.0},
        )
        assert VolatilityBreakout().evaluate(state) is None


class TestStrategyEvaluationService:
    def _state(self) -> MarketState:
        return MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"sma_20": 110.0, "sma_50": 100.0},
        )

    def test_evaluate_unknown_strategy_returns_none(self) -> None:
        from traderos.infrastructure.repositories.in_memory.strategies import (
            InMemoryStrategyRepository,
        )

        svc = StrategyEvaluationService(repo=InMemoryStrategyRepository())
        assert svc.evaluate(uuid.uuid4(), self._state()) is None

    def test_evaluate_unknown_template_returns_none(self) -> None:
        from traderos.domain.entities import Strategy
        from traderos.domain.entities import StrategyStatus
        from traderos.infrastructure.repositories.in_memory.strategies import (
            InMemoryStrategyRepository,
        )

        repo = InMemoryStrategyRepository()
        s = repo.add(
            Strategy(
                name="custom",
                params={},
                version="1.0.0",
                status=StrategyStatus.ACTIVE,
                template="no_such_template",
            )
        )
        svc = StrategyEvaluationService(repo=repo)
        assert svc.evaluate(s.id, self._state()) is None

    def test_evaluate_known_template_returns_signal(self) -> None:
        from traderos.domain.entities import Strategy
        from traderos.domain.entities import StrategyStatus
        from traderos.infrastructure.repositories.in_memory.strategies import (
            InMemoryStrategyRepository,
        )

        repo = InMemoryStrategyRepository()
        s = repo.add(
            Strategy(
                name="ma_tuned",
                params={"trend_threshold": 0.01},
                version="1.0.0",
                status=StrategyStatus.ACTIVE,
                template="moving_average_trend",
            )
        )
        svc = StrategyEvaluationService(repo=repo)
        result = svc.evaluate(s.id, self._state())
        assert result is not None
        assert result.direction == "long"

    def test_evaluate_all_runs_every_registered_strategy(self) -> None:
        from traderos.infrastructure.repositories.in_memory.strategies import (
            InMemoryStrategyRepository,
        )

        svc = StrategyEvaluationService(repo=InMemoryStrategyRepository())
        results = svc.evaluate_all(self._state())
        names = [name for name, _ in results]
        assert "moving_average_trend" in names

    def test_moving_average_trend_zero_slow(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"sma_20": 110.0, "sma_50": 0.0},
        )
        assert MovingAverageTrend().evaluate(state) is None

    def test_volatility_breakout_below_threshold(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"atr_14": 1.0, "close": 100.0},
        )
        assert VolatilityBreakout().evaluate(state) is None

    def test_mean_reversion_within_bands(self) -> None:
        state = MarketState(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            candles=[],
            indicators={"bb_upper_20": 110.0, "bb_lower_20": 90.0, "close": 100.0},
        )
        assert MeanReversion().evaluate(state) is None

    def test_registry_unregister(self) -> None:
        r = StrategyRegistry()
        r.register(MovingAverageTrend)
        r.unregister("moving_average_trend")
        assert r.get("moving_average_trend") is None

    def test_evaluate_all_skips_missing_class(self) -> None:
        from types import SimpleNamespace

        repo = SimpleNamespace(get=lambda _id: None)
        missing = SimpleNamespace(list=lambda: ["ghost"], get=lambda name: None)
        svc = StrategyEvaluationService(repo=repo, registry=missing)
        assert svc.evaluate_all(self._state()) == []
