from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum

from traderos.domain.exceptions import DomainError


class TradeSide(Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


_VALID_TRANSITIONS: dict[TradeStatus, set[TradeStatus]] = {
    TradeStatus.PENDING: {TradeStatus.SUBMITTED, TradeStatus.CANCELLED, TradeStatus.REJECTED},
    TradeStatus.SUBMITTED: {
        TradeStatus.ACKNOWLEDGED,
        TradeStatus.PARTIALLY_FILLED,
        TradeStatus.FILLED,
        TradeStatus.CANCELLED,
        TradeStatus.REJECTED,
    },
    TradeStatus.ACKNOWLEDGED: {
        TradeStatus.PARTIALLY_FILLED,
        TradeStatus.FILLED,
        TradeStatus.CANCELLED,
        TradeStatus.REJECTED,
        TradeStatus.EXPIRED,
    },
    TradeStatus.PARTIALLY_FILLED: {
        TradeStatus.FILLED,
        TradeStatus.CANCELLED,
        TradeStatus.REJECTED,
        TradeStatus.EXPIRED,
    },
    TradeStatus.FILLED: set(),
    TradeStatus.CANCELLED: set(),
    TradeStatus.REJECTED: set(),
    TradeStatus.EXPIRED: set(),
}


class InvalidTradeTransitionError(DomainError):
    def __init__(self, current: TradeStatus, target: TradeStatus) -> None:
        super().__init__(f"Cannot transition from {current.name} to {target.name}")
        self.current = current
        self.target = target


def _guard_transition(current: TradeStatus, target: TradeStatus) -> None:
    if target not in _VALID_TRANSITIONS.get(current, set()):
        raise InvalidTradeTransitionError(current, target)


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
    external_order_id: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def submit(self, external_order_id: str) -> None:
        _guard_transition(self.status, TradeStatus.SUBMITTED)
        self.status = TradeStatus.SUBMITTED
        self.external_order_id = external_order_id
        self.updated_at = datetime.now(tz=UTC)

    def partial_fill(self, fill_qty: float, fill_price: float) -> None:
        _guard_transition(self.status, TradeStatus.PARTIALLY_FILLED)
        self.status = TradeStatus.PARTIALLY_FILLED
        self.filled_quantity = fill_qty
        self.filled_price = fill_price
        self.updated_at = datetime.now(tz=UTC)

    def acknowledge(self) -> None:
        _guard_transition(self.status, TradeStatus.ACKNOWLEDGED)
        self.status = TradeStatus.ACKNOWLEDGED
        self.updated_at = datetime.now(tz=UTC)

    def fill(self, fill_qty: float, fill_price: float) -> None:
        _guard_transition(self.status, TradeStatus.FILLED)
        self.status = TradeStatus.FILLED
        self.filled_quantity = fill_qty
        self.filled_price = fill_price
        self.filled_at = datetime.now(tz=UTC)
        self.updated_at = self.filled_at

    def cancel(self) -> None:
        _guard_transition(self.status, TradeStatus.CANCELLED)
        self.status = TradeStatus.CANCELLED
        self.updated_at = datetime.now(tz=UTC)

    def reject(self) -> None:
        _guard_transition(self.status, TradeStatus.REJECTED)
        self.status = TradeStatus.REJECTED
        self.updated_at = datetime.now(tz=UTC)

    def expire(self) -> None:
        _guard_transition(self.status, TradeStatus.EXPIRED)
        self.status = TradeStatus.EXPIRED
        self.updated_at = datetime.now(tz=UTC)
