from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum


class StrategyStatus(Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


# Statuses the trading engine treats as "enabled" (Programme C operator
# catalog: enable/disable/promote/archive gate what actually runs).
ENABLED_STRATEGY_STATUSES = frozenset({StrategyStatus.ACTIVE, StrategyStatus.PROMOTED})


@dataclass(frozen=True)
class Strategy:
    name: str
    params: dict
    version: str
    status: StrategyStatus = StrategyStatus.DRAFT
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    template: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
