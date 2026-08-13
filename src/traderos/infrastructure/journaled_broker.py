"""Durable, idempotent order-submission wrapper (CLOSURE-12 / OT-002).

This adapter slots between the application and a live ``BrokerAdapter`` and
persists every order *intent* to the durable :class:`OrderEventJournal`
**before** the underlying broker is touched. On a process restart the wrapper
is rebuilt from the same journal, so re-presenting the same derived
idempotency key does **not** re-submit the order to the broker — the
journaled outcome is replayed instead. This is the core guarantee that lets a
real order survive a restart and a broker-side surprise.

Lifecycle per submitted order:

- ``intent`` is written to the journal (published=0) *before* the broker call.
  If the process dies mid-submit the durable intent survives and is surfaced
  by :meth:`pending` for broker reconciliation.
- ``confirmed`` updates the same key *after* the broker returns, carrying the
  external order id/fill/status so a later replay returns the real result.
- A repeat of the same request shape short-circuits the broker: a ``confirmed``
  key replays the stored result; an ``intent``-only key returns
  ``needs_reconcile`` so reconciliation resolves broker-side truth.

Read-only accessors (:meth:`get_account_balance`, :meth:`get_positions`,
:meth:`get_open_orders`) and order management (:meth:`modify_order`,
:meth:`cancel_order`) pass straight through.
"""

from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.journal import OrderEventJournal

_NS = uuid.UUID("bbf79853-1b7a-4eea-9f07-bec4a4848e1f")

INTENT = "intent"
CONFIRMED = "confirmed"


def _client_key(market_id: Any, side: str, quantity: float, method: str) -> str:
    return str(uuid.uuid5(_NS, f"order:{market_id}:{side}:{quantity}:{method}"))


def _to_result(payload: dict[str, Any]) -> FillResult:
    return FillResult(
        filled=bool(payload.get("filled", False)),
        fill_quantity=float(payload.get("fill_quantity", 0.0)),
        fill_price=float(payload.get("fill_price", 0.0)),
        remaining=float(payload.get("remaining", 0.0)),
        status=str(payload.get("status", "rejected")),
        order_id=str(payload.get("order_id", "")),
    )


class JournaledBroker(BrokerAdapter):
    """Idempotent submit-are decorator over a live broker."""

    def __init__(
        self,
        broker: Any,
        journal: OrderEventJournal | None,
        *,
        disable: bool = False,
    ) -> None:
        self._broker = broker
        self._journal = None if disable else journal

    def _submit(self, method_name: str, *args: Any, **kwargs: Any) -> FillResult:
        market_id = kwargs.get("market_id") if "market_id" in kwargs else (args[0] if args else "")
        side = str(kwargs.get("side") if "side" in kwargs else (args[1] if len(args) > 1 else ""))
        raw_qty = (
            kwargs.get("quantity") if "quantity" in kwargs else (args[2] if len(args) > 2 else 0.0)
        )
        quantity = float(raw_qty) if raw_qty is not None else 0.0
        method = getattr(self._broker, method_name)

        if self._journal is None:
            return method(*args, **kwargs)

        # A caller-provided id is the authoritative idempotency key: it uniquely
        # identifies one *logical* order, so a repeated request with the same id
        # replays the stored outcome instead of double-submitting, while two
        # distinct orders that merely share a request shape never collide.
        cid = kwargs.get("client_order_id")
        key = cid or _client_key(market_id, side, quantity, method_name)
        existing = self._journal.get(key)
        if existing is not None:
            if existing["status"] == CONFIRMED:
                return _to_result(existing["payload"])
            return FillResult(False, 0.0, 0.0, quantity, "needs_reconcile")

        self._journal.record(
            key,
            key,
            INTENT,
            {
                "method": method_name,
                "market_id": str(market_id),
                "side": side,
                "quantity": quantity,
                "client_order_id": cid or "",
            },
        )

        result = method(*args, **kwargs)

        self._journal.update(
            key,
            CONFIRMED,
            {
                "filled": bool(result.filled),
                "fill_quantity": float(result.fill_quantity),
                "fill_price": float(result.fill_price),
                "remaining": float(result.remaining),
                "status": result.status,
                "order_id": result.order_id,
            },
        )
        self._journal.mark_published(key)
        return result

    def pending(self) -> list[dict[str, Any]]:
        """Intents persisted but never confirmed — durable drift to reconcile."""
        if self._journal is None:
            return []
        return [
            {"id": eid, "status": status, **payload}
            for eid, status, payload in self._journal.pending_events()
            if payload.get("method")
        ]

    # ---- broker port ----------------------------------------------------
    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        return self._submit(
            "place_market_order",
            market_id,
            side,
            quantity,
            close_price=close_price,
            client_order_id=client_order_id,
        )

    def place_flatten_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        return self._submit(
            "place_flatten_order",
            market_id,
            side,
            quantity,
            close_price=close_price,
        )

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        return self._submit(
            "place_limit_order", market_id, side, quantity, price=price, close_price=close_price
        )

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult:
        return self._submit(
            "place_stop_order",
            market_id,
            side,
            quantity,
            stop_price=stop_price,
            market_price=market_price,
        )

    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult:
        return self._submit(
            "place_trailing_stop_order",
            market_id,
            side,
            quantity,
            trail_percent=trail_percent,
            market_price=market_price,
        )

    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult:
        return self._broker.modify_order(
            order_id,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_percent=trail_percent,
        )

    def cancel_order(self, order_id: str) -> FillResult:
        return self._broker.cancel_order(order_id)

    def get_account_balance(self) -> float:
        return self._broker.get_account_balance()

    def get_positions(self) -> list[dict]:
        return self._broker.get_positions()

    def get_open_orders(self) -> list[dict]:
        return self._broker.get_open_orders()
