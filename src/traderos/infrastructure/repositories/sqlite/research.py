from __future__ import annotations

import sqlite3
import uuid
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
from traderos.infrastructure.repositories.sqlite.base import SQLiteRepository
from traderos.infrastructure.repositories.sqlite.base import from_json
from traderos.infrastructure.repositories.sqlite.base import to_dt
from traderos.infrastructure.repositories.sqlite.base import to_json
from traderos.infrastructure.repositories.sqlite.base import to_uuid


class SQLiteObservationRepository(SQLiteRepository[Observation], ObservationRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "observations"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]'
            )
            """)

    def _to_row(self, entity: Observation) -> dict:
        return {
            "id": str(entity.id),
            "timestamp": entity.timestamp.isoformat(),
            "symbol": entity.symbol,
            "content": entity.content,
            "tags": to_json(entity.tags),
        }

    def _from_row(self, row: sqlite3.Row) -> Observation:
        tags = cast(list, from_json(row["tags"]) or [])
        return Observation(
            id=to_uuid(row["id"]),
            timestamp=to_dt(row["timestamp"]),
            symbol=row["symbol"],
            content=row["content"],
            tags=tags,
        )

    def get_by_symbol(self, symbol: str) -> list[Observation]:
        cursor = self.conn.execute(
            "SELECT * FROM observations WHERE symbol = ? ORDER BY timestamp",
            (symbol,),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteHypothesisRepository(SQLiteRepository[Hypothesis], HypothesisRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "hypotheses"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Hypothesis) -> dict:
        return {
            "id": str(entity.id),
            "observation_id": str(entity.observation_id),
            "content": entity.content,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Hypothesis:
        return Hypothesis(
            id=to_uuid(row["id"]),
            observation_id=to_uuid(row["observation_id"]),
            content=row["content"],
            status=HypothesisStatus(row["status"]),
            created_at=to_dt(row["created_at"]),
        )

    def get_by_observation(self, observation_id: uuid.UUID) -> list[Hypothesis]:
        cursor = self.conn.execute(
            "SELECT * FROM hypotheses WHERE observation_id = ? ORDER BY created_at",
            (str(observation_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteExperimentRepository(SQLiteRepository[Experiment], ExperimentRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "experiments"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL,
                params TEXT NOT NULL DEFAULT '{}',
                results TEXT,
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Experiment) -> dict:
        return {
            "id": str(entity.id),
            "hypothesis_id": str(entity.hypothesis_id),
            "params": to_json(entity.params),
            "results": to_json(entity.results) if entity.results else None,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Experiment:
        params = cast(dict, from_json(row["params"]) or {})
        results = cast(dict, from_json(row["results"])) if row["results"] else None
        return Experiment(
            id=to_uuid(row["id"]),
            hypothesis_id=to_uuid(row["hypothesis_id"]),
            params=params,
            results=results,
            created_at=to_dt(row["created_at"]),
        )

    def get_by_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]:
        cursor = self.conn.execute(
            "SELECT * FROM experiments WHERE hypothesis_id = ? ORDER BY created_at",
            (str(hypothesis_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteExperimentResultRepository(
    SQLiteRepository[ExperimentResult], ExperimentResultRepository
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "experiment_results"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_results (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                metrics TEXT NOT NULL DEFAULT '{}',
                visual_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: ExperimentResult) -> dict:
        return {
            "id": str(entity.id),
            "experiment_id": str(entity.experiment_id),
            "metrics": to_json(entity.metrics),
            "visual_path": entity.visual_path,
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> ExperimentResult:
        metrics = cast(dict, from_json(row["metrics"]) or {})
        return ExperimentResult(
            id=to_uuid(row["id"]),
            experiment_id=to_uuid(row["experiment_id"]),
            metrics=metrics,
            visual_path=row["visual_path"],
            created_at=to_dt(row["created_at"]),
        )

    def get_by_experiment(self, experiment_id: uuid.UUID) -> list[ExperimentResult]:
        cursor = self.conn.execute(
            "SELECT * FROM experiment_results WHERE experiment_id = ? ORDER BY created_at",
            (str(experiment_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]


class SQLiteLessonRepository(SQLiteRepository[Lesson], LessonRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)

    @property
    def _table_name(self) -> str:
        return "lessons"

    def _create_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY,
                result_id TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """)

    def _to_row(self, entity: Lesson) -> dict:
        return {
            "id": str(entity.id),
            "result_id": str(entity.result_id),
            "content": entity.content,
            "tags": to_json(entity.tags),
            "created_at": entity.created_at.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> Lesson:
        tags = cast(list, from_json(row["tags"]) or [])
        return Lesson(
            id=to_uuid(row["id"]),
            result_id=to_uuid(row["result_id"]),
            content=row["content"],
            tags=tags,
            created_at=to_dt(row["created_at"]),
        )

    def get_by_result(self, result_id: uuid.UUID) -> list[Lesson]:
        cursor = self.conn.execute(
            "SELECT * FROM lessons WHERE result_id = ? ORDER BY created_at",
            (str(result_id),),
        )
        return [self._from_row(row) for row in cursor.fetchall()]

    def get_by_tags(self, tags: list[str]) -> list[Lesson]:
        result: list[Lesson] = []
        for tag in tags:
            like = f"%{tag}%"
            cursor = self.conn.execute("SELECT * FROM lessons WHERE tags LIKE ?", (like,))
            result.extend(self._from_row(row) for row in cursor.fetchall())
        return result
