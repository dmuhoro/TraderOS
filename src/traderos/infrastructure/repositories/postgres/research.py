from __future__ import annotations

import uuid
from typing import Any
from typing import cast

from traderos.domain.entities import Experiment
from traderos.domain.entities import ExperimentResult
from traderos.domain.entities import Hypothesis
from traderos.domain.entities import HypothesisStatus
from traderos.domain.entities import Lesson
from traderos.domain.entities import Observation
from traderos.domain.repositories.research_repository import ExperimentRepository
from traderos.domain.repositories.research_repository import ExperimentResultRepository
from traderos.domain.repositories.research_repository import HypothesisRepository
from traderos.domain.repositories.research_repository import LessonRepository
from traderos.domain.repositories.research_repository import ObservationRepository
from traderos.infrastructure.repositories.postgres.base import PostgresRepository
from traderos.infrastructure.repositories.postgres.base import from_json
from traderos.infrastructure.repositories.postgres.base import to_dt
from traderos.infrastructure.repositories.postgres.base import to_json
from traderos.infrastructure.repositories.postgres.base import to_uuid


class PostgresObservationRepository(PostgresRepository[Observation], ObservationRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "observations"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: Observation) -> dict:
        return {
            "id": str(entity.id),
            "timestamp": entity.timestamp.isoformat(),
            "symbol": entity.symbol,
            "content": entity.content,
            "tags": to_json(entity.tags),
        }

    def _from_row(self, row: Any) -> Observation:
        tags = cast(list, from_json(row[4]) or [])
        return Observation(
            id=to_uuid(row[0]),
            timestamp=to_dt(row[1]),
            symbol=row[2],
            content=row[3],
            tags=tags,
        )

    def get_by_symbol(self, symbol: str) -> list[Observation]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM observations WHERE symbol = %s ORDER BY timestamp",
                (symbol,),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresHypothesisRepository(PostgresRepository[Hypothesis], HypothesisRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "hypotheses"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: Hypothesis) -> dict:
        return {
            "id": str(entity.id),
            "observation_id": str(entity.observation_id),
            "content": entity.content,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Hypothesis:
        return Hypothesis(
            id=to_uuid(row[0]),
            observation_id=to_uuid(row[1]),
            content=row[2],
            status=HypothesisStatus(row[3]),
            created_at=to_dt(row[4]),
        )

    def get_by_observation(self, observation_id: uuid.UUID) -> list[Hypothesis]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM hypotheses WHERE observation_id = %s ORDER BY created_at",
                (str(observation_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresExperimentRepository(PostgresRepository[Experiment], ExperimentRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "experiments"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    hypothesis_id TEXT NOT NULL,
                    params TEXT NOT NULL DEFAULT '{}',
                    results TEXT,
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: Experiment) -> dict:
        return {
            "id": str(entity.id),
            "hypothesis_id": str(entity.hypothesis_id),
            "params": to_json(entity.params),
            "results": to_json(entity.results) if entity.results else None,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Experiment:
        params = cast(dict, from_json(row[2]) or {})
        results = cast(dict, from_json(row[3])) if row[3] else None
        return Experiment(
            id=to_uuid(row[0]),
            hypothesis_id=to_uuid(row[1]),
            params=params,
            results=results,
            created_at=to_dt(row[4]),
        )

    def get_by_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiments WHERE hypothesis_id = %s ORDER BY created_at",
                (str(hypothesis_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresExperimentResultRepository(
    PostgresRepository[ExperimentResult], ExperimentResultRepository
):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "experiment_results"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    metrics TEXT NOT NULL DEFAULT '{}',
                    visual_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: ExperimentResult) -> dict:
        return {
            "id": str(entity.id),
            "experiment_id": str(entity.experiment_id),
            "metrics": to_json(entity.metrics),
            "visual_path": entity.visual_path,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> ExperimentResult:
        metrics = cast(dict, from_json(row[2]) or {})
        return ExperimentResult(
            id=to_uuid(row[0]),
            experiment_id=to_uuid(row[1]),
            metrics=metrics,
            visual_path=row[3],
            created_at=to_dt(row[4]),
        )

    def get_by_experiment(self, experiment_id: uuid.UUID) -> list[ExperimentResult]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiment_results WHERE experiment_id = %s ORDER BY created_at",
                (str(experiment_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]


class PostgresLessonRepository(PostgresRepository[Lesson], LessonRepository):
    def __init__(self, connection: Any) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "lessons"

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """)
        self.conn.commit()

    def _to_row(self, entity: Lesson) -> dict:
        return {
            "id": str(entity.id),
            "result_id": str(entity.result_id),
            "content": entity.content,
            "tags": to_json(entity.tags),
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: Any) -> Lesson:
        tags = cast(list, from_json(row[3]) or [])
        return Lesson(
            id=to_uuid(row[0]),
            result_id=to_uuid(row[1]),
            content=row[2],
            tags=tags,
            created_at=to_dt(row[4]),
        )

    def get_by_result(self, result_id: uuid.UUID) -> list[Lesson]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM lessons WHERE result_id = %s ORDER BY created_at",
                (str(result_id),),
            )
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_tags(self, tags: list[str]) -> list[Lesson]:
        result: list[Lesson] = []
        for tag in tags:
            like = f"%{tag}%"
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM lessons WHERE tags LIKE %s", (like,))
                rows = cur.fetchall()
            result.extend(self._from_row(row) for row in rows)
        return result
