from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from traderos.domain.services.operator_workflow import OperatorWorkflow


class OperatorWorkflowRepository(ABC):
    """Persists the single live operator workflow so it survives restarts."""

    @abstractmethod
    def load(self) -> OperatorWorkflow | None: ...

    @abstractmethod
    def save(self, workflow: OperatorWorkflow) -> None: ...
