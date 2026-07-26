from __future__ import annotations

import uuid
from abc import abstractmethod

from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import Lesson
from traderos.domain.entities import Observation
from traderos.domain.repositories.base import Repository


class ObservationRepository(Repository[Observation]):
    @abstractmethod
    def get_by_symbol(self, symbol: str) -> list[Observation]: ...


class HypothesisRepository(Repository[Hypothesis]):
    @abstractmethod
    def get_by_observation(self, observation_id: uuid.UUID) -> list[Hypothesis]: ...


class ExperimentRepository(Repository[Experiment]):
    @abstractmethod
    def get_by_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]: ...


class ExperimentResultRepository(Repository[ExperimentResult]):
    @abstractmethod
    def get_by_experiment(self, experiment_id: uuid.UUID) -> list[ExperimentResult]: ...


class LessonRepository(Repository[Lesson]):
    @abstractmethod
    def get_by_result(self, result_id: uuid.UUID) -> list[Lesson]: ...

    @abstractmethod
    def get_by_tags(self, tags: list[str]) -> list[Lesson]: ...
