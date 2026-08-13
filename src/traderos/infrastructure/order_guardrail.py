from __future__ import annotations

import os
import uuid

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult

_ENABLED_VAR = "TRADEROS_ORDER_GUARDRAIL_ENABLED"
_MIN_QTY_VAR = "TRADEROS_MIN_ORDER_QTY"
_MAX_NOTIONAL_VAR = "TRADEROS_MAX_ORDER_NOTIONAL"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


class GuardrailedBroker(BrokerAdapter):
    """Rejects orders whose size breaches configured guardrails.

    Enforced in front of the real broker so a mis-sized signal can never reach
    the exchange during the pilot. Rejections return a rejected ``FillResult``
    instead of raising, so upstream risk/kill-switch handling (which records
    failures) still applies.
    """

    def __init__(
        self,
        inner: BrokerAdapter,
        min_order_qty: float | None = None,
        max_order_notional: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._inner = inner
        self._enabled = _env_bool(_ENABLED_VAR, True) if enabled is None else enabled
        self._min_qty = (
            float(os.getenv(_MIN_QTY_VAR, "1.0")) if min_order_qty is None else min_order_qty
        )
        self._max_notional = (
            float(os.getenv(_MAX_NOTIONAL_VAR, "500.0"))
            if max_order_notional is None
            else max_order_notional
        )

    def _reject(self, quantity: float, reason: str) -> FillResult:
        return FillResult(False, 0.0, 0.0, quantity, "rejected", reason)

    def _guard(self, quantity: float, price: float | None) -> FillResult | None:
        if not self._enabled:
            return None
        if quantity <= 0 or quantity < self._min_qty:
            return self._reject(
                quantity,
                f"Order quantity {quantity} below minimum {self._min_qty}",
            )
        if price is not None and self._max_notional > 0 and quantity * price > self._max_notional:
            return self._reject(
                quantity,
                f"Order notional {quantity * price:.2f} exceeds maximum {self._max_notional}",
            )
        return None

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        rejected = self._guard(quantity, close_price)
        if rejected is not None:
            return rejected
        return self._inner.place_market_order(
            market_id, side, quantity, close_price, client_order_id
        )

    def place_flatten_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        # Kill-switch closes must never be refused by size policy: a flatten
        # closes exactly the exposure we hold, so the notional/min-qty guards
        # (which protect against mis-sized *strategy* orders) would only block
        # the emergency exit. Delegate straight through.
        return self._inner.place_flatten_order(market_id, side, quantity, close_price)

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        rejected = self._guard(quantity, price)
        if rejected is not None:
            return rejected
        return self._inner.place_limit_order(market_id, side, quantity, price, close_price)

    def cancel_order(self, order_id: str) -> FillResult:
        return self._inner.cancel_order(order_id)

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult:
        rejected = self._guard(quantity, stop_price)
        if rejected is not None:
            return rejected
        return self._inner.place_stop_order(market_id, side, quantity, stop_price, market_price)

    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult:
        rejected = self._guard(quantity, market_price)
        if rejected is not None:
            return rejected
        return self._inner.place_trailing_stop_order(
            market_id, side, quantity, trail_percent, market_price
        )

    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult:
        if qty is not None:
            rejected = self._guard(qty, limit_price or stop_price)
            if rejected is not None:
                return rejected
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
