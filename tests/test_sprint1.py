import os
import unittest

import numpy as np
import pandas as pd

from traderos.domain.backtesting.engine import BacktestEngine
from traderos.domain.risk.engine import RiskEngine
from traderos.domain.strategies.strategies import MovingAverageTrend
from traderos.infrastructure.database.db_manager import DatabaseManager


class TestTraderOSSprint1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists("test_sprint1.db"):
            os.remove("test_sprint1.db")
        os.environ["DB_PATH"] = "test_sprint1.db"
        cls.db = DatabaseManager()
        cls.bt_engine = BacktestEngine(cls.db)
        cls.risk = RiskEngine(cls.db)

    def test_strategy_signal_generation(self):
        strategy = MovingAverageTrend()
        # Create crossover data
        dates = pd.date_range(start="2023-01-01", periods=100, freq="h")
        price = np.concatenate([np.linspace(100, 110, 50), np.linspace(110, 90, 50)])
        df = pd.DataFrame(
            {
                "timestamp": dates,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 100,
                "symbol": "TEST",
            }
        )
        df = strategy.generate_signal(df)
        self.assertIn("signal", df.columns)
        self.assertTrue(any(df["signal"] != 0))

    def test_backtest_execution(self):
        strategy = MovingAverageTrend()
        dates = pd.date_range(start="2023-01-01", periods=200, freq="h")
        price = 100 + np.random.normal(0, 1, 200).cumsum()
        df = pd.DataFrame(
            {
                "timestamp": dates,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 100,
                "symbol": "TEST",
            }
        )
        results = self.bt_engine.run_backtest(strategy, df)
        self.assertIn("metrics", results)
        self.assertIn("total_return", results["metrics"])

    def test_risk_position_sizing(self):
        # 100k capital, 2% volatility
        size = self.risk.calculate_position_size(100000, 0.02, 0.01)
        # (100000 * 0.01) / 0.02 = 50000
        # But max_position_size is 0.10 (10%)
        # 10% of 100k = 10000
        self.assertEqual(size, 10000)

    def test_risk_kill_switch(self):
        # Breach drawdown limit (-15% default)
        self.assertTrue(self.risk.check_kill_switch(-0.20, 0.5))
        # Breach correlation limit (0.85 default)
        self.assertTrue(self.risk.check_kill_switch(-0.05, 0.90))
        # Safe conditions
        self.assertFalse(self.risk.check_kill_switch(-0.05, 0.5))

    def test_risk_exposure_validation(self):
        # Total capital 100k, current exposure 40k, new 5k = 45% (Safe < 50%)
        self.assertTrue(self.risk.validate_exposure(40000, 5000, 100000))
        # Total 100k, current 40k, new 15k = 55% (Unsafe > 50%)
        self.assertFalse(self.risk.validate_exposure(40000, 15000, 100000))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        if os.path.exists("test_sprint1.db"):
            os.remove("test_sprint1.db")


if __name__ == "__main__":
    unittest.main()
