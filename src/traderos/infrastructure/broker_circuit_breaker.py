"""Broker adapter wrapper that routes every order through the circuit breaker.

Composed at the boundary in ``factory.py`` after the rate limiter / guardrails,
so the breaker fails fast exactly where production orders are submitted —
regardless of which caller (cycle executor, paper service, flatten service,
probe) touches the broker.
"""

from __future__ import annotations

import uuid

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.resilience import BROKER_CB
from traderos.infrastructure.resilience import with_circuit_breaker


class CircuitBreakeredBroker(BrokerAdapter):
    """A ``BrokerAdapter`` whose order-modifying calls trip ``BROKER_CB``.

    Delegates every method to ``inner``. Order submit/cancel paths are the
    protected surface (a hung or failing broker must stop being hammered);
    read-only queries pass through untouched.
    """

    def __init__(self, inner: BrokerAdapter) -> None:
        self._inner = inner

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        return self._inner.place_market_order(
            market_id, side, quantity, close_price, client_order_id
        )

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        return self._inner.place_limit_order(market_id, side, quantity, price, close_price)

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def cancel_order(self, order_id: str) -> FillResult:
        return self._inner.cancel_order(order_id)

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult:
        return self._inner.place_stop_order(market_id, side, quantity, stop_price, market_price)

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult:
        return self._inner.place_trailing_stop_order(
            market_id, side, quantity, trail_percent, market_price
        )

    @with_circuit_breaker(BROKER_CB, timeout=5.0)
    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult:
        return self._inner.modify_order(
            order_id,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_percent=trail_percent,
        )

    def get_account_balance(self) -> float:
        return self._inner.get_account_balance()

    def get_positions(self) -> list[dict]:
        return self._inner.get_positions()

    def get_open_orders(self) -> list[dict]:
        return self._inner.get_open_orders()
