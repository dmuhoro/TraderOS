from __future__ import annotations

import sqlite3

from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.operator_workflow import WorkflowStatus
from traderos.domain.services.operator_workflow import WorkflowTransition
from traderos.infrastructure.repositories.sqlite.base import to_dt


class SQLiteOperatorWorkflowRepository(OperatorWorkflowRepository):
    """Single-row workflow persistence (id = 1) so the live operator
    workflow survives restarts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS operator_workflow (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_step TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                session_id TEXT,
                started_at TEXT,
                completed_at TEXT
            )
            """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER NOT NULL,
                from_step TEXT,
                to_step TEXT NOT NULL,
                actor TEXT NOT NULL,
                result TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """)
        self.conn.commit()

    def load(self) -> OperatorWorkflow | None:
        row = self.conn.execute("SELECT * FROM operator_workflow WHERE id = 1").fetchone()
        if row is None:
            return None
        transitions = self.conn.execute(
            "SELECT * FROM workflow_transitions WHERE workflow_id = 1 ORDER BY id"
        ).fetchall()
        workflow = OperatorWorkflow(
            current_step=OperatorStep(row["current_step"]) if row["current_step"] else None,
            status=WorkflowStatus(row["status"]),
            session_id=row["session_id"],
            started_at=to_dt(row["started_at"]) if row["started_at"] else None,
            completed_at=to_dt(row["completed_at"]) if row["completed_at"] else None,
        )
        workflow.transitions = [
            WorkflowTransition(
                from_step=OperatorStep(t["from_step"]) if t["from_step"] else None,
                to_step=OperatorStep(t["to_step"]),
                actor=t["actor"],
                result=t["result"],
                timestamp=to_dt(t["timestamp"]),
            )
            for t in transitions
        ]
        return workflow

    def save(self, workflow: OperatorWorkflow) -> None:
        current_step = workflow.current_step.value if workflow.current_step else None
        self.conn.execute(
            """
            INSERT INTO operator_workflow
                (id, current_step, status, session_id, started_at, completed_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_step = excluded.current_step,
                status = excluded.status,
                session_id = excluded.session_id,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                current_step,
                workflow.status.value,
                workflow.session_id,
                workflow.started_at.isoformat() if workflow.started_at else None,
                workflow.completed_at.isoformat() if workflow.completed_at else None,
            ),
        )
        self.conn.execute("DELETE FROM workflow_transitions WHERE workflow_id = 1")
        for t in workflow.transitions:
            self.conn.execute(
                """
                INSERT INTO workflow_transitions
                    (workflow_id, from_step, to_step, actor, result, timestamp)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    t.from_step.value if t.from_step else None,
                    t.to_step.value,
                    t.actor,
                    t.result,
                    t.timestamp.isoformat(),
                ),
            )
        self.conn.commit()
