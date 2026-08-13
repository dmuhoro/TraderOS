from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod
from typing import NamedTuple


class FillResult(NamedTuple):
    filled: bool
    fill_quantity: float
    fill_price: float
    remaining: float
    status: str
    order_id: str = ""


class BrokerAdapter(ABC):
    @abstractmethod
    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult: ...

    def place_flatten_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        """Emergency-close seam used by the kill switch / fatal freeze.

        Defaults to the ordinary market-order path so every real adapter keeps
        the full journal/circuit-breaker stack. Wrappers whose policy must
        NEVER throttle or refuse the kill switch (the broker rate limiter and
        the order-size guardrails) override this to bypass only their own
        policy while still delegating to the inner adapter's real submission
        path.
        """
        return self.place_market_order(market_id, side, quantity, close_price)

    @abstractmethod
    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> FillResult: ...

    @abstractmethod
    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult: ...

    @abstractmethod
    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult: ...

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult: ...

    @abstractmethod
    def get_account_balance(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def get_open_orders(self) -> list[dict]: ...
