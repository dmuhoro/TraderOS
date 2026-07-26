from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime


@dataclass(frozen=True)
class KnowledgeNode:
    label: str
    node_type: str
    content: str
    embedding: list[float] | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True)
class KnowledgeEdge:
    source_id: uuid.UUID
    target_id: uuid.UUID
    relationship: str
    weight: float = 1.0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
