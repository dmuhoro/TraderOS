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
    ) -> FillResult: ...

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
    def get_account_balance(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def get_open_orders(self) -> list[dict]: ...
