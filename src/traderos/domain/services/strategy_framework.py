from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Candle
from traderos.domain.repositories import StrategyRepository


class MarketState(NamedTuple):
    timestamp: datetime
    candles: list[Candle]
    indicators: dict[str, float]


class SignalResult(NamedTuple):
    direction: str
    confidence: float
    metadata: dict


class StrategyBase(ABC):
    name: str
    version: str = "1.0.0"

    @abstractmethod
    def evaluate(self, state: MarketState) -> SignalResult | None: ...


@dataclass
class StrategyRegistry:
    _strategies: dict[str, type[StrategyBase]] = field(default_factory=dict)

    def register(self, strategy_cls: type[StrategyBase]) -> type[StrategyBase]:
        self._strategies[strategy_cls.name] = strategy_cls
        return strategy_cls

    def get(self, name: str) -> type[StrategyBase] | None:
        return self._strategies.get(name)

    def list(self) -> list[str]:
        return list(self._strategies.keys())


registry = StrategyRegistry()


@registry.register
class MovingAverageTrend(StrategyBase):
    name = "moving_average_trend"
    version = "1.0.0"

    def evaluate(self, state: MarketState) -> SignalResult | None:
        fast = state.indicators.get("sma_20")
        slow = state.indicators.get("sma_50")
        if fast is None or slow is None or slow == 0:
            return None
        ratio = (fast - slow) / slow
        if ratio > 0.02:
            return SignalResult("long", min(ratio * 10, 1.0), {"fast": fast, "slow": slow})
        if ratio < -0.02:
            return SignalResult("short", min(abs(ratio) * 10, 1.0), {"fast": fast, "slow": slow})
        return None


@registry.register
class VolatilityBreakout(StrategyBase):
    name = "volatility_breakout"
    version = "1.0.0"

    def evaluate(self, state: MarketState) -> SignalResult | None:
        atr = state.indicators.get("atr_14")
        close = state.indicators.get("close")
        if atr is None or close is None or close == 0:
            return None
        if atr / close > 0.02:
            direction = "long" if state.indicators.get("sma_20", 0) > close else "short"
            return SignalResult(direction, min(atr / close * 5, 1.0), {"atr": atr, "close": close})
        return None


@registry.register
class MeanReversion(StrategyBase):
    name = "mean_reversion"
    version = "1.0.0"

    def evaluate(self, state: MarketState) -> SignalResult | None:
        bb_upper = state.indicators.get("bb_upper_20")
        bb_lower = state.indicators.get("bb_lower_20")
        close = state.indicators.get("close")
        if bb_upper is None or bb_lower is None or close is None:
            return None
        if close > bb_upper:
            distance = (close - bb_upper) / bb_upper
            return SignalResult(
                "short", min(distance * 5, 1.0), {"close": close, "upper": bb_upper}
            )
        if close < bb_lower:
            distance = (bb_lower - close) / bb_lower
            return SignalResult("long", min(distance * 5, 1.0), {"close": close, "lower": bb_lower})
        return None


@dataclass
class StrategyEvaluationService:
    repo: StrategyRepository
    registry: StrategyRegistry = field(default_factory=lambda: registry)

    def evaluate(self, strategy_id: uuid.UUID, state: MarketState) -> SignalResult | None:
        strat = self.repo.get(strategy_id)
        if strat is None:
            return None
        strat_cls = self.registry.get(strat.name)
        if strat_cls is None:
            return None
        instance = strat_cls()
        return instance.evaluate(state)

    def evaluate_all(self, state: MarketState) -> list[tuple[str, SignalResult]]:
        results: list[tuple[str, SignalResult]] = []
        for name in self.registry.list():
            strat_cls = self.registry.get(name)
            if strat_cls is None:
                continue
            result = strat_cls().evaluate(state)
            if result is not None:
                results.append((name, result))
        return results
