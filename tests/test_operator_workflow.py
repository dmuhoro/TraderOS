from __future__ import annotations

import sqlite3

from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.operator_workflow import OPERATOR_STEPS
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.operator_workflow import WorkflowError
from traderos.domain.services.operator_workflow import WorkflowStatus
from traderos.infrastructure.repositories.in_memory import InMemoryOperatorWorkflowRepository
from traderos.infrastructure.repositories.sqlite import SQLiteOperatorWorkflowRepository


class TestOperatorWorkflowStateMachine:
    def test_initial_state_is_idle_and_points_to_start(self) -> None:
        workflow = OperatorWorkflow()
        assert workflow.current_step is None
        assert workflow.status == WorkflowStatus.IDLE
        assert workflow.next_step() == OperatorStep.START

    def test_first_transition_must_be_start(self) -> None:
        workflow = OperatorWorkflow()
        try:
            workflow.advance(OperatorStep.PREFLIGHT)
        except WorkflowError as exc:
            assert "must begin with" in str(exc)
        else:
            raise AssertionError("expected WorkflowError")

    def test_advancing_through_all_steps_in_order(self) -> None:
        workflow = OperatorWorkflow()
        for step in OPERATOR_STEPS:
            workflow.advance(step, actor="operator", result="ok")
        assert workflow.status == WorkflowStatus.COMPLETED
        assert workflow.current_step == OperatorStep.SESSION_REPORT
        assert workflow.completed_at is not None
        assert len(workflow.transitions) == len(OPERATOR_STEPS)

    def test_skipping_a_step_is_rejected(self) -> None:
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START)
        try:
            workflow.advance(OperatorStep.PAPER_TRADING)
        except WorkflowError as exc:
            assert "expected" in str(exc)
        else:
            raise AssertionError("expected WorkflowError")

    def test_rerunning_current_step_is_allowed(self) -> None:
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START)
        workflow.advance(OperatorStep.PREFLIGHT)
        workflow.advance(OperatorStep.PREFLIGHT)
        assert workflow.current_step == OperatorStep.PREFLIGHT
        assert len(workflow.transitions) == 3

    def test_start_is_not_repeatable(self) -> None:
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START)
        try:
            workflow.advance(OperatorStep.START)
        except WorkflowError:
            pass
        else:
            raise AssertionError("expected WorkflowError")

    def test_completed_workflow_cannot_advance(self) -> None:
        workflow = OperatorWorkflow()
        for step in OPERATOR_STEPS:
            workflow.advance(step)
        try:
            workflow.advance(OperatorStep.START)
        except WorkflowError:
            pass
        else:
            raise AssertionError("expected WorkflowError")

    def test_status_becomes_running_after_first_advance(self) -> None:
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START)
        assert workflow.status == WorkflowStatus.RUNNING
        assert workflow.started_at is not None

    def test_bind_session(self) -> None:
        workflow = OperatorWorkflow()
        workflow.bind_session("session-42")
        assert workflow.session_id == "session-42"

    def test_reset_returns_to_idle(self) -> None:
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START)
        workflow.bind_session("session-42")
        workflow.reset()
        assert workflow.current_step is None
        assert workflow.status == WorkflowStatus.IDLE
        assert workflow.session_id is None
        assert workflow.transitions == []
        assert workflow.started_at is None
        assert workflow.completed_at is None


class TestOperatorWorkflowRepositories:
    def test_in_memory_roundtrip(self) -> None:
        repo: OperatorWorkflowRepository = InMemoryOperatorWorkflowRepository()
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START, actor="operator")
        workflow.advance(OperatorStep.PREFLIGHT, actor="operator", result="passed")
        repo.save(workflow)

        loaded = repo.load()
        assert loaded is not None
        assert loaded.current_step == OperatorStep.PREFLIGHT
        assert loaded.status == WorkflowStatus.RUNNING
        assert len(loaded.transitions) == 2

    def test_in_memory_load_when_empty(self) -> None:
        repo: OperatorWorkflowRepository = InMemoryOperatorWorkflowRepository()
        assert repo.load() is None


class TestSQLiteOperatorWorkflowRepository:
    def _repo(self) -> SQLiteOperatorWorkflowRepository:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return SQLiteOperatorWorkflowRepository(conn)

    def test_load_when_empty(self) -> None:
        assert self._repo().load() is None

    def test_roundtrip_preserves_full_workflow(self) -> None:
        repo = self._repo()
        workflow = OperatorWorkflow()
        workflow.advance(OperatorStep.START, actor="operator")
        workflow.advance(OperatorStep.PREFLIGHT, actor="operator", result="passed")
        workflow.advance(OperatorStep.BROKER_CHECK, actor="ops", result="connected")
        workflow.bind_session("session-7")
        repo.save(workflow)

        loaded = repo.load()
        assert loaded is not None
        assert loaded.current_step == OperatorStep.BROKER_CHECK
        assert loaded.status == WorkflowStatus.RUNNING
        assert loaded.session_id == "session-7"
        assert loaded.started_at is not None
        assert len(loaded.transitions) == 3
        assert loaded.transitions[-1].from_step == OperatorStep.PREFLIGHT
        assert loaded.transitions[-1].to_step == OperatorStep.BROKER_CHECK
        assert loaded.transitions[-1].actor == "ops"

    def test_save_overwrites_single_row(self) -> None:
        repo = self._repo()
        first = OperatorWorkflow()
        first.advance(OperatorStep.START)
        repo.save(first)

        second = OperatorWorkflow()
        second.advance(OperatorStep.START)
        second.advance(OperatorStep.PREFLIGHT)
        repo.save(second)

        loaded = repo.load()
        assert loaded is not None
        assert loaded.current_step == OperatorStep.PREFLIGHT
        assert len(loaded.transitions) == 2
        assert len(repo.conn.execute("SELECT * FROM operator_workflow").fetchall()) == 1

    def test_completed_workflow_roundtrip(self) -> None:
        repo = self._repo()
        workflow = OperatorWorkflow()
        for step in OPERATOR_STEPS:
            workflow.advance(step)
        repo.save(workflow)

        loaded = repo.load()
        assert loaded is not None
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.completed_at is not None
        assert len(loaded.transitions) == len(OPERATOR_STEPS)


class TestV006Migration:
    def test_creates_workflow_tables_on_fresh_db(self) -> None:
        from traderos.infrastructure.database.migration_manager import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "operator_workflow" in tables
        assert "workflow_transitions" in tables
        conn.close()

    def test_reconciles_legacy_strategies_table(self) -> None:
        from traderos.infrastructure.database.migration_manager import get_current_version
        from traderos.infrastructure.database.migration_manager import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE strategies ("
            " id INTEGER PRIMARY KEY, name TEXT UNIQUE, params_json TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO strategies (id, name, params_json, created_at) "
            "VALUES (1, 'legacy_strat', '{}', '2024-01-01')"
        )
        conn.commit()
        migrate(conn)

        columns = {r[1] for r in conn.execute("PRAGMA table_info(strategies)").fetchall()}
        assert "params" in columns
        assert "template" in columns
        assert "version" in columns
        assert "status" in columns
        assert "params_json" not in columns
        row = conn.execute("SELECT * FROM strategies").fetchone()
        assert row["name"] == "legacy_strat"
        assert row["params"] == "{}"
        assert row["version"] == "1.0.0"
        assert row["status"] == "draft"
        assert get_current_version(conn) >= 6
        conn.close()
