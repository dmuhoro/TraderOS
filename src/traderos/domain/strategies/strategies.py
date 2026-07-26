import pandas as pd

from traderos.domain.strategies.base_strategy import Strategy
from traderos.domain.strategies.base_strategy import registry


@registry.register
class MovingAverageTrend(Strategy):
    """Simple SMA Crossover Strategy."""

    def __init__(self, params=None):
        default_params = {"fast_period": 20, "slow_period": 50}
        super().__init__("MovingAverageTrend", {**default_params, **(params or {})})

    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["fast_sma"] = df["close"].rolling(window=self.params["fast_period"]).mean()
        df["slow_sma"] = df["close"].rolling(window=self.params["slow_period"]).mean()

        df["signal"] = 0
        df.loc[df["fast_sma"] > df["slow_sma"], "signal"] = 1
        df.loc[df["fast_sma"] < df["slow_sma"], "signal"] = -1
        return df

    def calculate_risk(self, df: pd.DataFrame) -> float:
        return 0.01  # Default 1% risk

    def define_exit(self, df: pd.DataFrame) -> dict:
        return {"stop_loss": 0.02, "take_profit": 0.04}


@registry.register
class VolatilityBreakout(Strategy):
    """Breakout strategy based on ATR bands."""

    def __init__(self, params=None):
        default_params = {"period": 20, "multiplier": 2.0}
        super().__init__("VolatilityBreakout", {**default_params, **(params or {})})

    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["sma"] = df["close"].rolling(window=self.params["period"]).mean()
        # Simple volatility measure
        df["std"] = df["close"].rolling(window=self.params["period"]).std()
        df["upper"] = df["sma"] + (df["std"] * self.params["multiplier"])
        df["lower"] = df["sma"] - (df["std"] * self.params["multiplier"])

        df["signal"] = 0
        df.loc[df["close"] > df["upper"], "signal"] = 1
        df.loc[df["close"] < df["lower"], "signal"] = -1
        return df

    def calculate_risk(self, df: pd.DataFrame) -> float:
        return 0.015

    def define_exit(self, df: pd.DataFrame) -> dict:
        return {"stop_loss": 0.015, "take_profit": 0.05}


@registry.register
class MeanReversion(Strategy):
    """RSI based mean reversion strategy."""

    def __init__(self, params=None):
        default_params = {"period": 14, "oversold": 30, "overbought": 70}
        super().__init__("MeanReversion", {**default_params, **(params or {})})

    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.params["period"]).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.params["period"]).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))

        df["signal"] = 0
        df.loc[df["rsi"] < self.params["oversold"], "signal"] = 1
        df.loc[df["rsi"] > self.params["overbought"], "signal"] = -1
        return df

    def calculate_risk(self, df: pd.DataFrame) -> float:
        return 0.005

    def define_exit(self, df: pd.DataFrame) -> dict:
        return {"stop_loss": 0.01, "take_profit": 0.02}
