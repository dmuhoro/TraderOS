from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import HypothesisStatus
from traderos.domain.entities import Lesson
from traderos.domain.entities import Observation
from traderos.domain.repositories import ExperimentRepository
from traderos.domain.repositories import ExperimentResultRepository
from traderos.domain.repositories import HypothesisRepository
from traderos.domain.repositories import LessonRepository
from traderos.domain.repositories import ObservationRepository


class WorkflowTrace(NamedTuple):
    observation: Observation | None
    hypothesis: Hypothesis | None
    experiment: Experiment | None
    result: ExperimentResult | None
    lesson: Lesson | None


@dataclass
class ResearchService:
    observations: ObservationRepository
    hypotheses: HypothesisRepository
    experiments: ExperimentRepository
    results: ExperimentResultRepository
    lessons: LessonRepository

    def create_observation(
        self,
        symbol: str,
        content: str,
        tags: list[str] | None = None,
    ) -> Observation:
        obs = Observation(
            timestamp=datetime.now(tz=UTC),
            symbol=symbol,
            content=content,
            tags=tags or [],
        )
        return self.observations.add(obs)

    def create_hypothesis(self, observation_id: uuid.UUID, content: str) -> Hypothesis:
        hyp = Hypothesis(observation_id=observation_id, content=content)
        return self.hypotheses.add(hyp)

    def start_experiment(
        self,
        hypothesis_id: uuid.UUID,
        params: dict,
    ) -> Experiment:
        hyp = self.hypotheses.get(hypothesis_id)
        if hyp is None:
            raise ValueError(f"Hypothesis {hypothesis_id} not found")
        if hyp.status != HypothesisStatus.PROPOSED:
            raise ValueError(f"Cannot start experiment for hypothesis in state {hyp.status}")
        self.hypotheses.update(
            Hypothesis(
                observation_id=hyp.observation_id,
                content=hyp.content,
                status=HypothesisStatus.TESTING,
                id=hyp.id,
                created_at=hyp.created_at,
            ),
        )
        exp = Experiment(hypothesis_id=hypothesis_id, params=params)
        return self.experiments.add(exp)

    def record_result(
        self,
        experiment_id: uuid.UUID,
        metrics: dict,
        visual_path: str = "",
    ) -> ExperimentResult:
        res = ExperimentResult(
            experiment_id=experiment_id,
            metrics=metrics,
            visual_path=visual_path,
        )
        return self.results.add(res)

    def conclude_hypothesis(
        self,
        hypothesis_id: uuid.UUID,
        status: HypothesisStatus,
    ) -> Hypothesis:
        terminal = {
            HypothesisStatus.CONFIRMED,
            HypothesisStatus.REJECTED,
            HypothesisStatus.INCONCLUSIVE,
        }
        if status not in terminal:
            raise ValueError(f"Invalid terminal status: {status}")
        hyp = self.hypotheses.get(hypothesis_id)
        if hyp is None:
            raise ValueError(f"Hypothesis {hypothesis_id} not found")
        updated = Hypothesis(
            observation_id=hyp.observation_id,
            content=hyp.content,
            status=status,
            id=hyp.id,
            created_at=hyp.created_at,
        )
        return self.hypotheses.update(updated)

    def extract_lesson(
        self,
        result_id: uuid.UUID,
        content: str,
        tags: list[str] | None = None,
    ) -> Lesson:
        lesson = Lesson(result_id=result_id, content=content, tags=tags or [])
        return self.lessons.add(lesson)

    def get_observations_by_symbol(self, symbol: str) -> list[Observation]:
        return self.observations.get_by_symbol(symbol)

    def get_hypotheses_for_observation(
        self,
        observation_id: uuid.UUID,
    ) -> list[Hypothesis]:
        return self.hypotheses.get_by_observation(observation_id)

    def get_experiments_for_hypothesis(
        self,
        hypothesis_id: uuid.UUID,
    ) -> list[Experiment]:
        return self.experiments.get_by_hypothesis(hypothesis_id)

    def get_results_for_experiment(
        self,
        experiment_id: uuid.UUID,
    ) -> list[ExperimentResult]:
        return self.results.get_by_experiment(experiment_id)

    def get_lessons_for_result(self, result_id: uuid.UUID) -> list[Lesson]:
        return self.lessons.get_by_result(result_id)

    def trace_workflow(self, lesson_id: uuid.UUID) -> WorkflowTrace:
        lesson = self.lessons.get(lesson_id)
        if lesson is None:
            return WorkflowTrace(None, None, None, None, None)
        result = self.results.get(lesson.result_id) if lesson else None
        experiment = self.experiments.get(result.experiment_id) if result else None
        hypothesis = self.hypotheses.get(experiment.hypothesis_id) if experiment else None
        observation = self.observations.get(hypothesis.observation_id) if hypothesis else None
        return WorkflowTrace(
            observation=observation,
            hypothesis=hypothesis,
            experiment=experiment,
            result=result,
            lesson=lesson,
        )
