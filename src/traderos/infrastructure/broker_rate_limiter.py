from __future__ import annotations

import os
import uuid

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.rate_limiter import RateLimiter

_ENABLED_VAR = "BROKER_RATE_LIMIT_ENABLED"
_MAX_VAR = "BROKER_RATE_LIMIT_MAX"
_WINDOW_VAR = "BROKER_RATE_LIMIT_WINDOW"


def _is_enabled() -> bool:
    return os.getenv(_ENABLED_VAR, "").lower() in ("true", "1", "yes")


class RateLimitExceededError(Exception):
    pass


class RateLimitedBroker(BrokerAdapter):
    """Proxy around a BrokerAdapter that applies per-method rate limits.

    Flagged off by default. Enable via BROKER_RATE_LIMIT_ENABLED=true.
    """

    def __init__(
        self,
        inner: BrokerAdapter,
        max_requests: int | None = None,
        window_seconds: float | None = None,
    ) -> None:
        self._inner = inner
        self._enabled = _is_enabled()
        max_r = max_requests if max_requests is not None else int(os.getenv(_MAX_VAR, "10"))
        window = (
            window_seconds if window_seconds is not None else float(os.getenv(_WINDOW_VAR, "1.0"))
        )
        self._limiter = RateLimiter(max_requests=max_r, window_seconds=window)

    def _check(self, method: str) -> None:
        if not self._enabled:
            return
        if not self._limiter.check(method):
            raise RateLimitExceededError(
                f"Rate limit exceeded for broker method '{method}' "
                f"(max {self._limiter.max_requests}/{self._limiter.window_seconds}s)"
            )

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        self._check("place_market_order")
        return self._inner.place_market_order(market_id, side, quantity, close_price)

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        self._check("place_limit_order")
        return self._inner.place_limit_order(market_id, side, quantity, price, close_price)

    def cancel_order(self, order_id: str) -> FillResult:
        self._check("cancel_order")
        return self._inner.cancel_order(order_id)

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult:
        self._check("place_stop_order")
        return self._inner.place_stop_order(market_id, side, quantity, stop_price, market_price)

    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult:
        self._check("place_trailing_stop_order")
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
        self._check("modify_order")
        return self._inner.modify_order(
            order_id,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_percent=trail_percent,
        )

    def get_account_balance(self) -> float:
        self._check("get_account_balance")
        return self._inner.get_account_balance()

    def get_positions(self) -> list[dict]:
        self._check("get_positions")
        return self._inner.get_positions()

    def get_open_orders(self) -> list[dict]:
        self._check("get_open_orders")
        return self._inner.get_open_orders()
