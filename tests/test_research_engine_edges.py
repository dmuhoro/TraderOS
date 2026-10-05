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
            -- This fixture must mirror the real schema (v001 /
            -- repositories/sqlite/research.py, which agree) in every column,
            -- including id types. Two rounds of drift hid real defects here:
            --
            --   1. It declared no timestamp column, so it passed while the real
            --      schema rejected the same INSERT with a NOT NULL violation.
            --   2. It declared id INTEGER PRIMARY KEY, which is a rowid alias.
            --      The engine now supplies TEXT ids, so an INTEGER PRIMARY KEY
            --      raised "datatype mismatch" -- a fixture-only failure that the
            --      production schema would never produce.
            --
            -- Keep this in sync with the migration; a fixture that disagrees
            -- with production is worse than no fixture.
            CREATE TABLE observations (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                symbol TEXT, content TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE hypotheses (
                id TEXT PRIMARY KEY, observation_id TEXT NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
                created_at TEXT NOT NULL
            );
            -- research_tests/research_results/lessons carry no created_at; the
            -- migration (v001, "Legacy research pair") gives research_tests a
            -- results_summary column instead. Earlier revisions of this fixture
            -- added a created_at to all three, so the fixture demanded a column
            -- production does not have.
            CREATE TABLE research_tests (
                id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL,
                test_params TEXT, results_summary TEXT
            );
            CREATE TABLE research_results (
                id TEXT PRIMARY KEY, test_id TEXT NOT NULL,
                metrics_json TEXT, visual_path TEXT
            );
            CREATE TABLE lessons (
                id TEXT PRIMARY KEY, result_id TEXT NOT NULL,
                content TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
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
            lambda engine: engine.create_hypothesis("obs-id", "hyp"),
            lambda engine: engine.create_test("hyp-id", {"period": "90d"}),
            lambda engine: engine.record_result("test-id", {"win_rate": 0.5}),
            lambda engine: engine.record_lesson("result-id", "lesson"),
        ],
    )
    def test_write_failure_raises(self, call) -> None:
        """A failed INSERT must raise, not report a phantom success.

        This used to assert that a null ``cursor.lastrowid`` raises. That
        assertion encoded the bug rather than the contract: the engine read its
        ids from lastrowid, so on a TEXT primary key it *always* got None -- the
        "Failed to create" path was the only path. Ids are now generated in
        Python, so the real contract is that a write which does not succeed
        raises, and lastrowid is irrelevant either way.
        """
        conn = MagicMock()
        cursor = conn.conn.cursor.return_value
        cursor.execute.side_effect = sqlite3.OperationalError("no such table")
        with pytest.raises(sqlite3.OperationalError):
            call(ResearchEngine(conn))
