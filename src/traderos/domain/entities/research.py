from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum


class HypothesisStatus(Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Observation:
    timestamp: datetime
    symbol: str
    content: str
    tags: list[str]
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class Hypothesis:
    observation_id: uuid.UUID
    content: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True)
class Experiment:
    hypothesis_id: uuid.UUID
    params: dict
    results: dict | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: uuid.UUID
    metrics: dict
    visual_path: str = ""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True)
class Lesson:
    result_id: uuid.UUID
    content: str
    tags: list[str]
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
