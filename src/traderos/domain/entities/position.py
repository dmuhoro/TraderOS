from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime


@dataclass(frozen=False)
class Position:
    market_id: uuid.UUID
    quantity: float
    entry_price: float
    current_price: float
    pnl: float
    realized_pnl: float = 0.0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def update_price(self, price: float) -> None:
        self.current_price = price
        self.pnl = self.quantity * (price - self.entry_price)
        self.updated_at = datetime.now(tz=UTC)

    def close(self, close_price: float) -> float:
        realized = self.quantity * (close_price - self.entry_price)
        self.realized_pnl += realized
        self.quantity = 0.0
        self.current_price = close_price
        self.pnl = 0.0
        self.updated_at = datetime.now(tz=UTC)
        return realized
