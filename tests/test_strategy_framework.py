from __future__ import annotations

from datetime import UTC
from datetime import datetime

from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import MeanReversion
from traderos.domain.services.strategy_framework import MovingAverageTrend
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
