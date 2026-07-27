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


@dataclass(frozen=False)
class Trade:
    signal_id: uuid.UUID
    market_id: uuid.UUID
    side: TradeSide
    quantity: float
    price: float
    status: TradeStatus = TradeStatus.PENDING
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    filled_at: datetime | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def fill(self, fill_qty: float, fill_price: float) -> None:
        self.status = TradeStatus.FILLED
        self.filled_quantity = fill_qty
        self.filled_price = fill_price
        self.filled_at = datetime.now(tz=UTC)
        self.updated_at = self.filled_at

    def cancel(self) -> None:
        self.status = TradeStatus.CANCELLED
        self.updated_at = datetime.now(tz=UTC)

    def reject(self) -> None:
        self.status = TradeStatus.REJECTED
        self.updated_at = datetime.now(tz=UTC)
