import json
import logging
import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.ports import DatabasePort

logger = logging.getLogger(__name__)


def _now() -> str:
    """ISO-8601 UTC timestamp string, the format the TEXT columns store."""
    return datetime.now(tz=UTC).isoformat()


class ResearchEngine:
    def __init__(self, db_manager: DatabasePort):
        self.db = db_manager

    def create_observation(self, symbol: str, content: str, tags: str = "") -> str:
        # Ids are generated here, not read from cursor.lastrowid, and the
        # timestamp is supplied explicitly. Both because every table this engine
        # writes is declared ``id TEXT PRIMARY KEY`` and ``timestamp TEXT NOT
        # NULL`` with no default, consistently by v001 and by
        # repositories/{sqlite,postgres}/research.py:
        #
        #   * an INSERT omitting id stores NULL, so lastrowid came back None and
        #     the caller received a null id for a row that did not exist;
        #   * an INSERT omitting timestamp raised
        #     ``IntegrityError: NOT NULL constraint failed``.
        #
        # The chain was therefore broken end to end: every id was null, so
        # get_full_workflow's join found nothing and silently returned {}. That
        # is a failure that looks like "no data yet" rather than like a bug.
        observation_id = uuid.uuid4().hex
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO observations (id, timestamp, symbol, content, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (observation_id, _now(), symbol, content, tags),
        )
        self.db.conn.commit()
        return observation_id

    def create_hypothesis(self, observation_id: str, content: str) -> str:
        hypothesis_id = uuid.uuid4().hex
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO hypotheses (id, observation_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (hypothesis_id, observation_id, content, _now()),
        )
        self.db.conn.commit()
        return hypothesis_id

    def create_test(self, hypothesis_id: str, params: dict, backtest_id: int | None = None) -> str:
        if backtest_id:
            params["backtest_id"] = backtest_id

        test_id = uuid.uuid4().hex
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO research_tests (id, hypothesis_id, test_params) VALUES (?, ?, ?)",
            (test_id, hypothesis_id, json.dumps(params)),
        )
        self.db.conn.commit()
        return test_id

    def record_result(self, test_id: str, metrics: dict, visual_path: str = "") -> str:
        result_id = uuid.uuid4().hex
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO research_results (id, test_id, metrics_json, visual_path) "
            "VALUES (?, ?, ?, ?)",
            (result_id, test_id, json.dumps(metrics), visual_path),
        )
        self.db.conn.commit()
        return result_id

    def record_lesson(self, result_id: str, content: str, tags: str = "") -> str:
        lesson_id = uuid.uuid4().hex
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO lessons (id, result_id, content, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lesson_id, result_id, content, tags, _now()),
        )
        self.db.conn.commit()
        return lesson_id

    def get_full_workflow(self, lesson_id: str) -> dict:
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
                "observation": row[0],
                "hypothesis": row[1],
                "test": json.loads(row[2]),
                "result": json.loads(row[3]),
                "lesson": row[4],
            }
        return {}
