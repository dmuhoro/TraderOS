import os
import unittest

import numpy as np
import pandas as pd

from analysis_engine.indicators import MarketAnalyzer
from database.db_manager import DatabaseManager
from journal_engine.research_engine import ResearchEngine


class TestTraderOS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use a test database
        if os.path.exists("test_trader.db"):
            os.remove("test_trader.db")
        os.environ["DB_PATH"] = "test_trader.db"
        cls.db = DatabaseManager()
        cls.research = ResearchEngine(cls.db)

    def test_regime_detection(self):
        # Create trending bullish data
        dates = pd.date_range(start="2023-01-01", periods=300, freq="h")
        price = np.linspace(100, 200, 300)
        df = pd.DataFrame(
            {
                "timestamp": dates,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 100,
            }
        )
        df = MarketAnalyzer.detect_regime(df)
        latest_regime = df.iloc[-1]["regime"]
        self.assertIn("Trending Bullish", latest_regime)

    def test_knowledge_graph_workflow(self):
        oid = self.research.create_observation("BTC/USDT", "Price rejected at 70k")
        hid = self.research.create_hypothesis(oid, "70k is a major supply zone")
        tid = self.research.create_test(hid, {"backtest_period": "90d"})
        rid = self.research.record_result(tid, {"win_rate": 0.65})
        lid = self.research.record_lesson(rid, "Wait for sweep before entry", "liquidity")

        workflow = self.research.get_full_workflow(lid)
        self.assertEqual(workflow["observation"], "Price rejected at 70k")
        self.assertEqual(workflow["lesson"], "Wait for sweep before entry")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        if os.path.exists("test_trader.db"):
            os.remove("test_trader.db")


if __name__ == "__main__":
    unittest.main()
