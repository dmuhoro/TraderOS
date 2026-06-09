from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Optional

class Strategy(ABC):
    def __init__(self, name: str, params: Optional[Dict] = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Input: OHLC DataFrame
        Output: DataFrame with a 'signal' column (1: Long, -1: Short, 0: Neutral)
        """
        pass

    @abstractmethod
    def calculate_risk(self, df: pd.DataFrame) -> float:
        """Calculate recommended risk per trade based on strategy logic."""
        pass

    @abstractmethod
    def define_exit(self, df: pd.DataFrame) -> Dict:
        """Define take profit and stop loss levels."""
        pass

class StrategyRegistry:
    def __init__(self):
        self._strategies = {}

    def register(self, strategy_cls):
        self._strategies[strategy_cls.__name__] = strategy_cls
        return strategy_cls

    def get_strategy(self, name: str, params: Optional[Dict] = None):
        if name in self._strategies:
            return self._strategies[name](params)
        raise ValueError(f"Strategy {name} not found in registry.")

    def list_strategies(self):
        return list(self._strategies.keys())

registry = StrategyRegistry()
