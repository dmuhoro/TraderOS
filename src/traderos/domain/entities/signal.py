from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum


class SignalDirection(Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Signal:
    market_id: uuid.UUID
    strategy_id: uuid.UUID
    direction: SignalDirection
    confidence: float
    generated_at: datetime
    expires_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0–1.0, got {self.confidence}")
        if self.expires_at <= self.generated_at:
            raise ValueError("expires_at must be after generated_at")
