"""Deterministic, idempotent broker-event coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.ports import AuditPort
from traderos.domain.ports import Event
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import MetricsPort


@dataclass
class OrderEventEngine:
    event_bus: EventBusPort
    audit: AuditPort | None = None
    metrics: MetricsPort | None = None
    persist: Callable[[Trade], None] | None = None
    portfolio_update: Callable[[Trade], None] | None = None

    def __post_init__(self) -> None:
        self._seen_events: set[str] = set()

    def apply(
        self,
        trade: Trade,
        status: TradeStatus,
        *,
        event_id: str = "",
        fill_quantity: float | None = None,
        fill_price: float | None = None,
        trace_id: str = "",
    ) -> bool:
        """Apply one broker event. Duplicate event ids are acknowledged but not replayed."""
        key = event_id or f"{trade.id}:{status.value}:{fill_quantity}:{fill_price}"
        if key in self._seen_events:
            return False
        old = trade.status
        if status == TradeStatus.ACKNOWLEDGED:
            trade.acknowledge()
        elif status == TradeStatus.PARTIALLY_FILLED:
            trade.partial_fill(fill_quantity or 0.0, fill_price or 0.0)
        elif status == TradeStatus.FILLED:
            trade.fill(fill_quantity or trade.quantity, fill_price or trade.price)
        elif status == TradeStatus.CANCELLED:
            trade.cancel()
        elif status == TradeStatus.REJECTED:
            trade.reject()
        elif status == TradeStatus.EXPIRED:
            trade.expire()
        elif status != old:
            raise ValueError(f"unsupported lifecycle transition: {old.value} -> {status.value}")
        self._seen_events.add(key)
        payload = {
            "trade_id": str(trade.id),
            "external_order_id": trade.external_order_id,
            "from": old.value,
            "to": trade.status.value,
            "filled_quantity": trade.filled_quantity,
            "filled_price": trade.filled_price,
            "event_id": key,
        }
        self.event_bus.publish(
            Event(
                "execution.order_status",
                payload,
                correlation_id=str(trade.id),
                trace_id=trace_id,
                market=str(trade.market_id),
                execution_context={"received_at": datetime.now(tz=UTC).isoformat()},
            )
        )
        if self.persist:
            self.persist(trade)
        if self.portfolio_update:
            self.portfolio_update(trade)
        if self.audit:
            self.audit.record(
                "order.status_transition",
                "order-event-engine",
                str(trade.id),
                f"{old.value}->{trade.status.value} event={key}",
            )
        if self.metrics:
            self.metrics.counter(f"orders.transition.{trade.status.value}")
        return True
