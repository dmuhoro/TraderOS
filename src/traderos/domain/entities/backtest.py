from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime

from traderos.domain.entities.value_objects import EquityCurve
from traderos.domain.entities.value_objects import Metrics


@dataclass(frozen=True)
class BacktestResult:
    strategy_id: uuid.UUID
    market_id: uuid.UUID
    metrics: Metrics
    equity_curve: EquityCurve
    period_start: datetime
    period_end: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
