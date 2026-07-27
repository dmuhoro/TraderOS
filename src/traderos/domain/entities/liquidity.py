from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum


class ZoneType(Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass(frozen=True)
class LiquidityZone:
    market_id: uuid.UUID
    price_level: float
    zone_type: ZoneType
    strength: int
    detected_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)
