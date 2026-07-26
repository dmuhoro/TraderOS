from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime


@dataclass(frozen=True)
class Position:
    market_id: uuid.UUID
    quantity: float
    entry_price: float
    current_price: float
    pnl: float
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
