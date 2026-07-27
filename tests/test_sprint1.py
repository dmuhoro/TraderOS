import os
import unittest

from traderos.infrastructure.database.db_manager import DatabaseManager


class TestTraderOSSDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists("test_sprint1.db"):
            os.remove("test_sprint1.db")
        os.environ["DB_PATH"] = "test_sprint1.db"
        cls.db = DatabaseManager()

    def test_db_connect(self):
        self.assertIsNotNone(self.db.conn)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        if os.path.exists("test_sprint1.db"):
            os.remove("test_sprint1.db")
