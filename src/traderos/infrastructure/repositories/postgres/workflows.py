from __future__ import annotations

from typing import Any

from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.operator_workflow import WorkflowStatus
from traderos.domain.services.operator_workflow import WorkflowTransition
from traderos.infrastructure.repositories.postgres.base import to_dt


class PostgresOperatorWorkflowRepository(OperatorWorkflowRepository):
    """Single-row workflow persistence (id = 1) so the live operator workflow
    survives restarts, on the PostgreSQL backend."""

    def __init__(self, connection: Any) -> None:
        self.conn = connection
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_workflow (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_step TEXT,
                    status TEXT NOT NULL DEFAULT 'idle',
                    session_id TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workflow_transitions (
                    id SERIAL PRIMARY KEY,
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
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM operator_workflow WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("SELECT * FROM workflow_transitions WHERE workflow_id = 1 ORDER BY id")
            transitions = cur.fetchall()
        workflow = OperatorWorkflow(
            current_step=OperatorStep(row[1]) if row[1] else None,
            status=WorkflowStatus(row[2]),
            session_id=row[3],
            started_at=to_dt(row[4]) if row[4] else None,
            completed_at=to_dt(row[5]) if row[5] else None,
        )
        workflow.transitions = [
            WorkflowTransition(
                from_step=OperatorStep(t[2]) if t[2] else None,
                to_step=OperatorStep(t[3]),
                actor=t[4],
                result=t[5],
                timestamp=to_dt(t[6]),
            )
            for t in transitions
        ]
        return workflow

    def save(self, workflow: OperatorWorkflow) -> None:
        current_step = workflow.current_step.value if workflow.current_step else None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO operator_workflow
                    (id, current_step, status, session_id, started_at, completed_at)
                VALUES (1, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    current_step = EXCLUDED.current_step,
                    status = EXCLUDED.status,
                    session_id = EXCLUDED.session_id,
                    started_at = EXCLUDED.started_at,
                    completed_at = EXCLUDED.completed_at
                """,
                (
                    current_step,
                    workflow.status.value,
                    workflow.session_id,
                    workflow.started_at.isoformat() if workflow.started_at else None,
                    workflow.completed_at.isoformat() if workflow.completed_at else None,
                ),
            )
            cur.execute("DELETE FROM workflow_transitions WHERE workflow_id = 1")
            for t in workflow.transitions:
                cur.execute(
                    """
                    INSERT INTO workflow_transitions
                        (workflow_id, from_step, to_step, actor, result, timestamp)
                    VALUES (1, %s, %s, %s, %s, %s)
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
