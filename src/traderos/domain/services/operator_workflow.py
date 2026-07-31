from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum

from traderos.domain.exceptions import DomainError

# Canonical operator workflow (Programme C, WP-C2). Advancing is strictly
# ordered: you may only move to the immediate next step, or re-run the current
# step (e.g. re-check a failing preflight). Anything else raises WorkflowError —
# the workflow is impossible to execute out of order.


class OperatorStep(Enum):
    START = "start"
    PREFLIGHT = "preflight"
    BROKER_CHECK = "broker_check"
    MARKET_DATA_CHECK = "market_data_check"
    PAPER_TRADING = "paper_trading"
    PERFORMANCE_REVIEW = "performance_review"
    STRATEGY_PROMOTION = "strategy_promotion"
    CONTROLLED_LIVE = "controlled_live"
    SHUTDOWN = "shutdown"
    SESSION_REPORT = "session_report"


OPERATOR_STEPS: tuple[OperatorStep, ...] = tuple(step for step in OperatorStep)
_STEP_INDEX = {step: idx for idx, step in enumerate(OPERATOR_STEPS)}

# The two terminal-ish steps that are never "re-runnable" in place.
_NON_REPEATABLE = frozenset({OperatorStep.START, OperatorStep.SESSION_REPORT})


class WorkflowStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"


class WorkflowError(DomainError):
    """Raised when an operator attempts an out-of-order workflow transition."""


@dataclass(frozen=True)
class WorkflowTransition:
    from_step: OperatorStep | None
    to_step: OperatorStep
    actor: str
    result: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class OperatorWorkflow:
    """Pure state machine for the operator session lifecycle.

    ``current_step`` starts as ``None`` (not yet started). The first valid
    transition is to ``OperatorStep.START``; from there every step must follow
    the canonical order. Re-running the current step is allowed so checks can
    be repeated until they pass.
    """

    current_step: OperatorStep | None = None
    status: WorkflowStatus = WorkflowStatus.IDLE
    session_id: str | None = None
    transitions: list[WorkflowTransition] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def next_step(self) -> OperatorStep | None:
        if self.current_step is None:
            return OperatorStep.START
        idx = _STEP_INDEX[self.current_step]
        if idx + 1 >= len(OPERATOR_STEPS):
            return None
        return OPERATOR_STEPS[idx + 1]

    def can_advance_to(self, step: OperatorStep | None) -> bool:
        if step is None or self.status == WorkflowStatus.COMPLETED:
            return False
        if step == self.current_step:
            return step not in _NON_REPEATABLE
        return step == self.next_step()

    def advance(
        self,
        step: OperatorStep,
        actor: str = "operator",
        result: str = "",
    ) -> OperatorWorkflow:
        if not self.can_advance_to(step):
            if self.current_step is None:
                hint = f"must begin with {OperatorStep.START.value}"
            else:
                following = self.next_step()
                next_label = following.value if following else "workflow complete"
                hint = f"expected {next_label}"
            raise WorkflowError(
                f"Cannot advance to {step.value} from "
                f"{self.current_step.value if self.current_step else 'none'} — {hint}"
            )
        if self.status == WorkflowStatus.IDLE:
            self.status = WorkflowStatus.RUNNING
            self.started_at = datetime.now(tz=UTC)
        self.transitions.append(
            WorkflowTransition(
                from_step=self.current_step,
                to_step=step,
                actor=actor,
                result=result,
            )
        )
        self.current_step = step
        if step == OperatorStep.START:
            pass  # session id assigned by the caller via ``session_id``
        if step == OperatorStep.SESSION_REPORT:
            self.status = WorkflowStatus.COMPLETED
            self.completed_at = datetime.now(tz=UTC)
        return self

    def bind_session(self, session_id: str) -> None:
        """Attach a session identifier when the workflow starts."""
        self.session_id = session_id

    def reset(self) -> OperatorWorkflow:
        self.current_step = None
        self.status = WorkflowStatus.IDLE
        self.session_id = None
        self.transitions = []
        self.started_at = None
        self.completed_at = None
        return self
