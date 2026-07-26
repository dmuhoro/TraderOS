from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import NamedTuple


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Order:
    market_id: uuid.UUID
    side: str
    order_type: OrderType
    quantity: float
    price: float | None
    stop_price: float | None
    status: OrderStatus = OrderStatus.PENDING
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None


class FillResult(NamedTuple):
    filled: bool
    fill_quantity: float
    fill_price: float
    remaining: float
    status: OrderStatus


@dataclass
class ExecutionService:
    slippage_model: str = "fixed"
    slippage_bps: float = 5.0

    def create_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
    ) -> Order:
        return Order(
            market_id=market_id,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=None,
            stop_price=None,
        )

    def create_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
    ) -> Order:
        return Order(
            market_id=market_id,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            stop_price=None,
        )

    def create_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
    ) -> Order:
        return Order(
            market_id=market_id,
            side=side,
            order_type=OrderType.STOP,
            quantity=quantity,
            price=None,
            stop_price=stop_price,
        )

    def apply_slippage(self, price: float) -> float:
        if self.slippage_bps == 0:
            return price
        return price * (1 + self.slippage_bps / 10000)

    def process_market_order(
        self,
        order: Order,
        market_price: float,
    ) -> FillResult:
        if order.order_type != OrderType.MARKET:
            return FillResult(False, 0, 0, order.quantity, OrderStatus.REJECTED)
        fill_price = self.apply_slippage(market_price)
        return FillResult(
            filled=True,
            fill_quantity=order.quantity,
            fill_price=fill_price,
            remaining=0.0,
            status=OrderStatus.FILLED,
        )

    def process_limit_order(
        self,
        order: Order,
        market_price: float,
    ) -> FillResult:
        if order.order_type != OrderType.LIMIT or order.price is None:
            return FillResult(False, 0, 0, order.quantity, OrderStatus.REJECTED)
        can_fill = (order.side == "buy" and market_price <= order.price) or (
            order.side == "sell" and market_price >= order.price
        )
        if not can_fill:
            return FillResult(False, 0, 0, order.quantity, OrderStatus.PENDING)
        return FillResult(
            filled=True,
            fill_quantity=order.quantity,
            fill_price=order.price,
            remaining=0.0,
            status=OrderStatus.FILLED,
        )

    def process_stop_order(
        self,
        order: Order,
        market_price: float,
    ) -> FillResult:
        if order.order_type != OrderType.STOP or order.stop_price is None:
            return FillResult(False, 0, 0, order.quantity, OrderStatus.REJECTED)
        triggered = (order.side == "buy" and market_price >= order.stop_price) or (
            order.side == "sell" and market_price <= order.stop_price
        )
        if not triggered:
            return FillResult(False, 0, 0, order.quantity, OrderStatus.PENDING)
        fill_price = self.apply_slippage(market_price)
        return FillResult(
            filled=True,
            fill_quantity=order.quantity,
            fill_price=fill_price,
            remaining=0.0,
            status=OrderStatus.FILLED,
        )

    def cancel_order(self, order: Order) -> Order:
        return Order(
            market_id=order.market_id,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            status=OrderStatus.CANCELLED,
            id=order.id,
            created_at=order.created_at,
            filled_quantity=order.filled_quantity,
            avg_fill_price=order.avg_fill_price,
        )
