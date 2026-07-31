from __future__ import annotations

from copy import deepcopy

from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.operator_workflow import OperatorWorkflow


class InMemoryOperatorWorkflowRepository(OperatorWorkflowRepository):
    def __init__(self, workflow: OperatorWorkflow | None = None) -> None:
        self._workflow = workflow

    def load(self) -> OperatorWorkflow | None:
        return deepcopy(self._workflow) if self._workflow else None

    def save(self, workflow: OperatorWorkflow) -> None:
        self._workflow = deepcopy(workflow)
