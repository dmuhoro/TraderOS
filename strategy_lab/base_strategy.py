from abc import ABC
from abc import abstractmethod

import pandas as pd


class Strategy(ABC):
    def __init__(self, name: str, params: dict | None = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Input: OHLC DataFrame
        Output: DataFrame with a 'signal' column (1: Long, -1: Short, 0: Neutral)
        """

    @abstractmethod
    def calculate_risk(self, df: pd.DataFrame) -> float:
        """Calculate recommended risk per trade based on strategy logic."""

    @abstractmethod
    def define_exit(self, df: pd.DataFrame) -> dict:
        """Define take profit and stop loss levels."""


class StrategyRegistry:
    def __init__(self):
        self._strategies = {}

    def register(self, strategy_cls):
        self._strategies[strategy_cls.__name__] = strategy_cls
        return strategy_cls

    def get_strategy(self, name: str, params: dict | None = None):
        if name in self._strategies:
            return self._strategies[name](params)
        raise ValueError(f"Strategy {name} not found in registry.")

    def list_strategies(self):
        return list(self._strategies.keys())


registry = StrategyRegistry()
