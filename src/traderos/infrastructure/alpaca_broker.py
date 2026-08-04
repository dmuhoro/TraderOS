from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
from traderos.infrastructure.retry import retry_with_backoff


def _new_client_order_id() -> str:
    """Stable idempotency key for one logical order intent.

    Generated once *before* the first submit attempt and reused verbatim on
    every retry, so a dropped response/timeout after the broker accepted the
    order can never cause a duplicate order at the broker (Alpaca dedupes by
    ``client_order_id`` within the day).
    """
    return str(uuid.uuid4())


_has_alpaca: bool
try:
    from alpaca.trading.client import TradingClient as _TradingClient
    from alpaca.trading.enums import OrderSide as _OrderSide
    from alpaca.trading.enums import OrderType as _OrderType
    from alpaca.trading.enums import QueryOrderStatus as _QueryOrderStatus
    from alpaca.trading.enums import TimeInForce as _TimeInForce
    from alpaca.trading.requests import GetOrdersRequest as _GetOrdersRequest
    from alpaca.trading.requests import MarketOrderRequest as _MarketOrderRequest
    from alpaca.trading.requests import ReplaceOrderRequest as _ReplaceOrderRequest
    from alpaca.trading.requests import StopOrderRequest as _StopOrderRequest
    from alpaca.trading.requests import TrailingStopOrderRequest as _TrailingStopOrderRequest

    _has_alpaca = True
except ImportError:
    _has_alpaca = False
    _TradingClient = None  # type: ignore[assignment]
    _MarketOrderRequest = None  # type: ignore[assignment]
    _ReplaceOrderRequest = None  # type: ignore[assignment]
    _StopOrderRequest = None  # type: ignore[assignment]
    _TrailingStopOrderRequest = None  # type: ignore[assignment]
    _OrderSide = None  # type: ignore[assignment]
    _OrderType = None  # type: ignore[assignment]
    _TimeInForce = None  # type: ignore[assignment]
    _GetOrdersRequest = None  # type: ignore[assignment]
    _QueryOrderStatus = None  # type: ignore[assignment]


class AlpacaBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        symbol_map: dict[uuid.UUID, str] | None = None,
        client: Any | None = None,
    ) -> None:
        if not _has_alpaca or _TradingClient is None:
            raise ImportError("alpaca-py is required. Install with: pip install alpaca-py")
        self._client: Any = client or _TradingClient(api_key, secret_key, paper=paper)
        self._symbol_map: dict[uuid.UUID, str] = symbol_map or {}

    def _symbol(self, market_id: uuid.UUID) -> str:
        return self._symbol_map.get(market_id, str(market_id))

    def _side(self, side: str) -> Any:
        if _OrderSide is None:
            raise InfrastructureError("alpaca-py OrderSide not available")
        return _OrderSide.BUY if side == "buy" else _OrderSide.SELL

    def _time_in_force(self) -> Any:
        if _TimeInForce is None:
            raise InfrastructureError("alpaca-py TimeInForce not available")
        return _TimeInForce.DAY

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        try:
            symbol = self._symbol(market_id)
            cid = client_order_id or _new_client_order_id()

            def _submit() -> Any:
                client = self._client
                req_cls = _MarketOrderRequest
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                if req_cls is None:
                    raise InfrastructureError("alpaca-py MarketOrderRequest not available")
                return client.submit_order(
                    order_data=req_cls(
                        symbol=symbol,
                        qty=quantity,
                        side=self._side(side),
                        time_in_force=self._time_in_force(),
                        client_order_id=cid,
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=True,
                fill_quantity=float(order.filled_qty or quantity),
                fill_price=float(order.filled_avg_price or 0.0),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        try:
            from alpaca.trading.requests import LimitOrderRequest

            symbol = self._symbol(market_id)
            cid = client_order_id or _new_client_order_id()

            def _submit() -> Any:
                client = self._client
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                return client.submit_order(
                    order_data=LimitOrderRequest(
                        symbol=symbol,
                        qty=quantity,
                        side=self._side(side),
                        time_in_force=self._time_in_force(),
                        limit_price=round(price, 2),
                        client_order_id=cid,
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=bool(order.filled_qty),
                fill_quantity=float(order.filled_qty or 0),
                fill_price=float(order.filled_avg_price or price),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled" if order.filled_qty == order.qty else "pending",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        try:
            symbol = self._symbol(market_id)
            cid = client_order_id or _new_client_order_id()

            def _submit() -> Any:
                client = self._client
                req_cls = _StopOrderRequest
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                if req_cls is None or _OrderType is None:
                    raise InfrastructureError("alpaca-py StopOrderRequest not available")
                return client.submit_order(
                    order_data=req_cls(
                        symbol=symbol,
                        qty=quantity,
                        side=self._side(side),
                        time_in_force=self._time_in_force(),
                        type=_OrderType.STOP,
                        stop_price=round(stop_price, 2),
                        client_order_id=cid,
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=bool(order.filled_qty),
                fill_quantity=float(order.filled_qty or 0),
                fill_price=float(order.filled_avg_price or 0.0),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled" if order.filled_qty == order.qty else "pending",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        try:
            symbol = self._symbol(market_id)
            cid = client_order_id or _new_client_order_id()

            def _submit() -> Any:
                client = self._client
                req_cls = _TrailingStopOrderRequest
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                if req_cls is None or _OrderType is None:
                    raise InfrastructureError("alpaca-py TrailingStopOrderRequest not available")
                return client.submit_order(
                    order_data=req_cls(
                        symbol=symbol,
                        qty=quantity,
                        side=self._side(side),
                        time_in_force=self._time_in_force(),
                        type=_OrderType.TRAILING_STOP,
                        trail_percent=round(trail_percent, 4),
                        client_order_id=cid,
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=bool(order.filled_qty),
                fill_quantity=float(order.filled_qty or 0),
                fill_price=float(order.filled_avg_price or 0.0),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled" if order.filled_qty == order.qty else "pending",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult:
        try:

            def _submit() -> Any:
                client = self._client
                req_cls = _ReplaceOrderRequest
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                if req_cls is None:
                    raise InfrastructureError("alpaca-py ReplaceOrderRequest not available")
                kwargs: dict[str, Any] = {}
                if qty is not None:
                    kwargs["qty"] = int(qty)
                if limit_price is not None:
                    kwargs["limit_price"] = round(limit_price, 2)
                if stop_price is not None:
                    kwargs["stop_price"] = round(stop_price, 2)
                if trail_percent is not None:
                    kwargs["trail"] = round(trail_percent, 4)
                if not kwargs:
                    raise InfrastructureError("no fields provided to modify order")
                return client.replace_order_by_id(order_id, order_data=req_cls(**kwargs))

            retry_with_backoff(_submit, max_retries=2)
            return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, 0.0, "rejected", str(e))

    def cancel_order(self, order_id: str) -> FillResult:
        try:
            self._client.cancel_order_by_id(order_id)
            return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, 0.0, "rejected", str(e))

    def get_account_balance(self) -> float:
        account = self._client.get_account()
        return float(account.equity)

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {"symbol": p.symbol, "qty": float(p.qty), "market_value": float(p.market_value)}
            for p in positions
        ]

    def get_open_orders(self) -> list[dict]:
        if _GetOrdersRequest is None or _QueryOrderStatus is None:
            raise ImportError("alpaca-py is required. Install with: pip install alpaca-py")
        orders = self._client.get_orders(_GetOrdersRequest(status=_QueryOrderStatus.OPEN))
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "qty": float(o.qty),
                "side": o.side,
                "type": o.type,
            }
            for o in orders
        ]
