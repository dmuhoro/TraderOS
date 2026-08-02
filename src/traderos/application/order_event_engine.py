"""Deterministic, idempotent broker-event coordinator.

Operational trust properties (Programme B):

- OT-002 durability: processed event ids are recorded in an optional durable
  journal so a process restart cannot lose duplicate-fill protection. The
  in-memory seen-set is preloaded from the journal at construction time.
- OT-003 atomic ordering: the transition is committed to the journal BEFORE
  any side effect; persistence then happens before publishing, and a publish
  failure leaves the event in the journal (unpublished) for a later replay.
- OT-006 serialization: events for one trade are applied under a per-trade
  lock so concurrent identical or out-of-order callbacks cannot race.
- OT-009 fill guards: duplicate fill ids are rejected and fill quantities are
  bounds-checked against the order quantity before the transition applies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from math import isfinite

from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.ports import AuditPort
from traderos.domain.ports import Event
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import MetricsPort
from traderos.infrastructure.journal import OrderEventJournal


@dataclass
class OrderEventEngine:
    event_bus: EventBusPort
    audit: AuditPort | None = None
    metrics: MetricsPort | None = None
    persist: Callable[[Trade], None] | None = None
    portfolio_update: Callable[[Trade], None] | None = None
    journal: OrderEventJournal | None = None

    def __post_init__(self) -> None:
        self._seen_events: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        if self.journal is not None:
            self._seen_events.update(self.journal.load_event_ids())

    def _lock_for(self, trade_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(trade_id, threading.Lock())

    @staticmethod
    def _validate_fill(
        status: TradeStatus,
        trade: Trade,
        fill_quantity: float | None,
        fill_price: float | None,
    ) -> None:
        if status not in (TradeStatus.PARTIALLY_FILLED, TradeStatus.FILLED):
            return
        qty = fill_quantity if fill_quantity is not None else trade.quantity
        price = fill_price if fill_price is not None else trade.price
        if not isfinite(qty) or qty <= 0:
            raise ValueError(
                f"invalid fill quantity {fill_quantity!r} for trade {trade.id}"
            )  # pragma: no cover
        if qty > trade.quantity:
            raise ValueError(
                f"fill quantity {qty} exceeds order quantity {trade.quantity} for"
                f" trade {trade.id}"
            )  # pragma: no cover
        if not isfinite(price) or price <= 0:
            raise ValueError(
                f"invalid fill price {fill_price!r} for trade {trade.id}"
            )  # pragma: no cover

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
        trade_id = str(trade.id)
        lock = self._lock_for(trade_id)
        with lock:
            if key in self._seen_events or (
                self.journal is not None and self.journal.contains(key)
            ):
                return False
            old = trade.status
            self._validate_fill(status, trade, fill_quantity, fill_price)
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
                raise ValueError(
                    f"unsupported lifecycle transition: {old.value} -> {status.value}"
                )  # pragma: no cover
            self._seen_events.add(key)
            payload = {
                "trade_id": trade_id,
                "external_order_id": trade.external_order_id,
                "from": old.value,
                "to": trade.status.value,
                "filled_quantity": trade.filled_quantity,
                "filled_price": trade.filled_price,
                "event_id": key,
            }
            event = Event(
                "execution.order_status",
                payload,
                correlation_id=trade_id,
                trace_id=trace_id,
                market=str(trade.market_id),
                execution_context={"received_at": datetime.now(tz=UTC).isoformat()},
            )
            if self.journal is not None:
                self.journal.record(
                    key, trade_id, trade.status.value, OrderEventJournal.encode(event)
                )
            if self.persist:
                self.persist(trade)
            if self.event_bus:
                self.event_bus.publish(event)
                if self.journal is not None:
                    self.journal.mark_published(key)
            if self.portfolio_update:
                self.portfolio_update(trade)
            if self.audit:
                self.audit.record(
                    "order.status_transition",
                    "order-event-engine",
                    trade_id,
                    f"{old.value}->{trade.status.value} event={key}",
                )
            if self.metrics:
                self.metrics.counter(f"orders.transition.{trade.status.value}")
            return True

    def replay(self) -> int:
        """Republish journaled events whose publish step never completed (OT-003 outbox).

        Returns the number of events re-published and marked done.
        """
        if self.journal is None:
            return 0
        count = 0
        for event_id, _status, envelope in self.journal.pending_events():
            event = OrderEventJournal.decode(envelope)
            self.event_bus.publish(event)
            self.journal.mark_published(event_id)
            count += 1
        return count

    @property
    def seen_count(self) -> int:
        return len(self._seen_events)
