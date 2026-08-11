from __future__ import annotations

import uuid

from traderos.domain.entities import HypothesisStatus
from traderos.domain.services.research_service import ResearchService
from traderos.infrastructure.repositories.in_memory.research import InMemoryExperimentRepository
from traderos.infrastructure.repositories.in_memory.research import (
    InMemoryExperimentResultRepository,
)
from traderos.infrastructure.repositories.in_memory.research import InMemoryHypothesisRepository
from traderos.infrastructure.repositories.in_memory.research import InMemoryLessonRepository
from traderos.infrastructure.repositories.in_memory.research import InMemoryObservationRepository


def _make_service() -> ResearchService:
    return ResearchService(
        observations=InMemoryObservationRepository(),
        hypotheses=InMemoryHypothesisRepository(),
        experiments=InMemoryExperimentRepository(),
        results=InMemoryExperimentResultRepository(),
        lessons=InMemoryLessonRepository(),
    )


class TestResearchService:
    def test_create_observation(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Saw a pattern", ["daily"])
        assert obs.symbol == "BTC/USDT"
        assert obs.content == "Saw a pattern"
        assert obs.tags == ["daily"]
        assert obs.id is not None

    def test_create_hypothesis(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "This happens every week")
        assert hyp.observation_id == obs.id
        assert hyp.content == "This happens every week"
        assert hyp.status == HypothesisStatus.PROPOSED

    def test_start_experiment_updates_hypothesis(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hypothesis")
        exp = svc.start_experiment(hyp.id, {"window": 20})
        assert exp.hypothesis_id == hyp.id
        assert exp.params == {"window": 20}
        updated = svc.hypotheses.get(hyp.id)
        assert updated is not None
        assert updated.status == HypothesisStatus.TESTING

    def test_start_experiment_rejects_non_proposed(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        svc.start_experiment(hyp.id, {})
        import pytest

        with pytest.raises(ValueError, match="TESTING"):
            svc.start_experiment(hyp.id, {})

    def test_start_experiment_unknown_hypothesis_raises(self) -> None:
        svc = _make_service()
        import pytest

        with pytest.raises(ValueError, match="not found"):
            svc.start_experiment(uuid.uuid4(), {})

    def test_conclude_unknown_hypothesis_raises(self) -> None:
        svc = _make_service()
        import pytest

        with pytest.raises(ValueError, match="not found"):
            svc.conclude_hypothesis(uuid.uuid4(), HypothesisStatus.CONFIRMED)

    def test_get_by_symbol(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern", ["daily"])
        fetched = svc.get_observations_by_symbol("BTC/USDT")
        assert [o.id for o in fetched] == [obs.id]

    def test_get_hypotheses_for_observation(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        fetched = svc.get_hypotheses_for_observation(obs.id)
        assert [h.id for h in fetched] == [hyp.id]

    def test_get_experiments_for_hypothesis(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        exp = svc.start_experiment(hyp.id, {"window": 14})
        fetched = svc.get_experiments_for_hypothesis(hyp.id)
        assert [e.id for e in fetched] == [exp.id]

    def test_get_results_for_experiment(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        exp = svc.start_experiment(hyp.id, {})
        res = svc.record_result(exp.id, {"sharpe": 1.5})
        fetched = svc.get_results_for_experiment(exp.id)
        assert [r.id for r in fetched] == [res.id]

    def test_get_lessons_for_result(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        exp = svc.start_experiment(hyp.id, {})
        res = svc.record_result(exp.id, {"sharpe": 1.5})
        lesson = svc.extract_lesson(res.id, "Works", ["confirmed"])
        fetched = svc.get_lessons_for_result(res.id)
        assert [lesson.id for lesson in fetched] == [lesson.id]

    def test_conclude_hypothesis(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        svc.start_experiment(hyp.id, {})
        concluded = svc.conclude_hypothesis(hyp.id, HypothesisStatus.CONFIRMED)
        assert concluded.status == HypothesisStatus.CONFIRMED

    def test_conclude_invalid_status_raises(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Pattern")
        hyp = svc.create_hypothesis(obs.id, "Hyp")
        import pytest

        with pytest.raises(ValueError, match="terminal"):
            svc.conclude_hypothesis(hyp.id, HypothesisStatus.TESTING)

    def test_full_workflow(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Saw pattern", ["daily"])
        hyp = svc.create_hypothesis(obs.id, "Weekly pattern")
        exp = svc.start_experiment(hyp.id, {"window": 14})
        res = svc.record_result(exp.id, {"sharpe": 1.5, "win_rate": 0.6})
        svc.conclude_hypothesis(hyp.id, HypothesisStatus.CONFIRMED)
        lesson = svc.extract_lesson(res.id, "This pattern works", ["confirmed"])
        assert lesson.result_id == res.id

    def test_trace_workflow(self) -> None:
        svc = _make_service()
        obs = svc.create_observation("BTC/USDT", "Saw pattern")
        hyp = svc.create_hypothesis(obs.id, "Weekly pattern")
        exp = svc.start_experiment(hyp.id, {"window": 14})
        res = svc.record_result(exp.id, {"sharpe": 1.5})
        svc.conclude_hypothesis(hyp.id, HypothesisStatus.CONFIRMED)
        lesson = svc.extract_lesson(res.id, "Works", ["confirmed"])
        trace = svc.trace_workflow(lesson.id)
        assert trace.observation is not None
        assert trace.observation.id == obs.id
        assert trace.hypothesis is not None
        assert trace.hypothesis.id == hyp.id
        assert trace.experiment is not None
        assert trace.experiment.id == exp.id
        assert trace.result is not None
        assert trace.result.id == res.id
        assert trace.lesson is not None
        assert trace.lesson.id == lesson.id

    def test_trace_unknown_id(self) -> None:
        svc = _make_service()
        trace = svc.trace_workflow(uuid.uuid4())
        assert trace.observation is None
        assert trace.hypothesis is None
        assert trace.experiment is None
        assert trace.result is None
        assert trace.lesson is None
