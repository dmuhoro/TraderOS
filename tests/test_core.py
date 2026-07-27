import os
import unittest

from traderos.domain.research.research_engine import ResearchEngine
from traderos.infrastructure.database.db_manager import DatabaseManager


class TestTraderOSCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists("test_trader.db"):
            os.remove("test_trader.db")
        os.environ["DB_PATH"] = "test_trader.db"
        cls.db = DatabaseManager()
        cls.research = ResearchEngine(cls.db)

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
