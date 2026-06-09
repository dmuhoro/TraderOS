import logging
import json
from datetime import datetime
from typing import List, Optional, Dict
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

class ResearchEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def create_observation(self, symbol: str, content: str, tags: str = "") -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO observations (symbol, content, tags) VALUES (?, ?, ?)",
            (symbol, content, tags)
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def create_hypothesis(self, observation_id: int, content: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO hypotheses (observation_id, content) VALUES (?, ?)",
            (observation_id, content)
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def create_test(self, hypothesis_id: int, params: Dict, backtest_id: Optional[int] = None) -> int:
        cursor = self.db.conn.cursor()
        # If backtest_id is provided, we can link it in test_params or a new column
        if backtest_id:
            params['backtest_id'] = backtest_id
            
        cursor.execute(
            "INSERT INTO research_tests (hypothesis_id, test_params) VALUES (?, ?)",
            (hypothesis_id, json.dumps(params))
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def record_result(self, test_id: int, metrics: Dict, visual_path: str = "") -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO research_results (test_id, metrics_json, visual_path) VALUES (?, ?, ?)",
            (test_id, json.dumps(metrics), visual_path)
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def record_lesson(self, result_id: int, content: str, tags: str = "") -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO lessons (result_id, content, tags) VALUES (?, ?, ?)",
            (result_id, content, tags)
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def get_full_workflow(self, lesson_id: int) -> Dict:
        """Trace back from a lesson to the original observation."""
        cursor = self.db.conn.cursor()
        query = """
            SELECT 
                o.content as observation,
                h.content as hypothesis,
                t.test_params as test,
                r.metrics_json as result,
                l.content as lesson
            FROM lessons l
            JOIN research_results r ON l.result_id = r.id
            JOIN research_tests t ON r.test_id = t.id
            JOIN hypotheses h ON t.hypothesis_id = h.id
            JOIN observations o ON h.observation_id = o.id
            WHERE l.id = ?
        """
        cursor.execute(query, (lesson_id,))
        row = cursor.fetchone()
        if row:
            return {
                'observation': row[0],
                'hypothesis': row[1],
                'test': json.loads(row[2]),
                'result': json.loads(row[3]),
                'lesson': row[4]
            }
        return {}
