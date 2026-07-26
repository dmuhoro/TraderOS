from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime


@dataclass(frozen=True)
class Indicator:
    market_id: uuid.UUID
    timestamp: datetime
    name: str
    value: float
    id: uuid.UUID = field(default_factory=uuid.uuid4)
