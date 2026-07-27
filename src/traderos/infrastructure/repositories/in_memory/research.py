from __future__ import annotations

import uuid

from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import Lesson
from traderos.domain.entities import Observation
from traderos.domain.repositories.research_repository import ExperimentRepository
from traderos.domain.repositories.research_repository import ExperimentResultRepository
from traderos.domain.repositories.research_repository import HypothesisRepository
from traderos.domain.repositories.research_repository import LessonRepository
from traderos.domain.repositories.research_repository import ObservationRepository
from traderos.infrastructure.repositories.in_memory.base import InMemoryRepository


class InMemoryObservationRepository(InMemoryRepository[Observation], ObservationRepository):
    def get_by_symbol(self, symbol: str) -> list[Observation]:
        return [o for o in self.list() if o.symbol == symbol]


class InMemoryHypothesisRepository(InMemoryRepository[Hypothesis], HypothesisRepository):
    def get_by_observation(self, observation_id: uuid.UUID) -> list[Hypothesis]:
        return [h for h in self.list() if h.observation_id == observation_id]


class InMemoryExperimentRepository(InMemoryRepository[Experiment], ExperimentRepository):
    def get_by_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]:
        return [e for e in self.list() if e.hypothesis_id == hypothesis_id]


class InMemoryExperimentResultRepository(
    InMemoryRepository[ExperimentResult], ExperimentResultRepository
):
    def get_by_experiment(self, experiment_id: uuid.UUID) -> list[ExperimentResult]:
        return [r for r in self.list() if r.experiment_id == experiment_id]


class InMemoryLessonRepository(InMemoryRepository[Lesson], LessonRepository):
    def get_by_result(self, result_id: uuid.UUID) -> list[Lesson]:
        return [lesson for lesson in self.list() if lesson.result_id == result_id]

    def get_by_tags(self, tags: list[str]) -> list[Lesson]:
        return [lesson for lesson in self.list() if any(t in lesson.tags for t in tags)]
