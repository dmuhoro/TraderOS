from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime

from traderos.domain.entities.value_objects import OHLCV
from traderos.domain.entities.value_objects import Timeframe


@dataclass(frozen=True)
class Candle:
    market_id: uuid.UUID
    ohlcv: OHLCV
    timestamp: datetime
    timeframe: Timeframe
    source: str = ""
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        self.ohlcv.validate()
