from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from traderos.domain.research.research_engine import ResearchEngine


class _FakeDB:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY, symbol TEXT, content TEXT, tags TEXT
            );
            CREATE TABLE hypotheses (
                id INTEGER PRIMARY KEY, observation_id INTEGER, content TEXT
            );
            CREATE TABLE research_tests (
                id INTEGER PRIMARY KEY, hypothesis_id INTEGER, test_params TEXT
            );
            CREATE TABLE research_results (
                id INTEGER PRIMARY KEY, test_id INTEGER, metrics_json TEXT, visual_path TEXT
            );
            CREATE TABLE lessons (
                id INTEGER PRIMARY KEY, result_id INTEGER, content TEXT, tags TEXT
            );
            """)


@pytest.fixture()
def engine() -> ResearchEngine:
    return ResearchEngine(_FakeDB())


class TestResearchEngineEdges:
    def test_create_test_with_backtest_id(self, engine: ResearchEngine) -> None:
        oid = engine.create_observation("BTCUSDT", "obs")
        hid = engine.create_hypothesis(oid, "hyp")
        tid = engine.create_test(hid, {"period": "90d"}, backtest_id=42)
        row = engine.db.conn.execute(
            "SELECT test_params FROM research_tests WHERE id = ?", (tid,)
        ).fetchone()
        assert json.loads(row[0]) == {"period": "90d", "backtest_id": 42}

    def test_full_workflow_returns_empty_for_unknown_lesson(self, engine: ResearchEngine) -> None:
        assert engine.get_full_workflow(999) == {}

    @pytest.mark.parametrize(
        "call",
        [
            lambda engine: engine.create_observation("BTCUSDT", "obs"),
            lambda engine: engine.create_hypothesis(1, "hyp"),
            lambda engine: engine.create_test(1, {"period": "90d"}),
            lambda engine: engine.record_result(1, {"win_rate": 0.5}),
            lambda engine: engine.record_lesson(1, "lesson"),
        ],
    )
    def test_lastrowid_none_raises(self, call) -> None:
        conn = MagicMock()
        cursor = conn.conn.cursor.return_value
        cursor.lastrowid = None
        with pytest.raises(RuntimeError, match="Failed to"):
            call(ResearchEngine(conn))
