from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum


class TradeSide(Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Trade:
    signal_id: uuid.UUID
    market_id: uuid.UUID
    side: TradeSide
    quantity: float
    price: float
    status: TradeStatus = TradeStatus.PENDING
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
